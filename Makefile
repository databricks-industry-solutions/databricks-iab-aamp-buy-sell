# AAMP accelerator — one-command front door.
# Today these wrap the data-seed CLI; as services/UI land they extend.

PY := .venv/bin/python

.PHONY: install preview validate up seed kv reset down frontend render deploy endpoints grant-apps grant-app-access run-apps register-sellers wire-buyer seed-job all taxonomies clean

taxonomies:             ## vendor IAB TSVs from a local SDK clone (SDK_DIR=path/to/seller-agent)
	@test -n "$(SDK_DIR)" || (echo "set SDK_DIR=path/to/seller-agent (a clone of the IAB SDK)"; exit 1)
	cp "$(SDK_DIR)/data/taxonomies/audience-1.1/Audience Taxonomy 1.1.tsv" resources/taxonomies/
	cp "$(SDK_DIR)/data/taxonomies/content-3.1/Content Taxonomy 3.1.tsv"  resources/taxonomies/
	@echo "✓ vendored IAB taxonomies → resources/taxonomies/ (buyer TaxonomyLookupTool + seed validator use these)"

install:                ## create venv + install deps
	python3 -m venv .venv && $(PY) -m pip install -q --upgrade pip && $(PY) -m pip install -q -r requirements.txt
	@echo "✓ installed. Next: cp config.example.yaml config.yaml, then 'make preview' or 'make up'."

validate:               ## generate + validate (single-channel sellers, no cross-seller, 3-way trust)
	$(PY) -m seed validate

preview:                ## generate + validate → inspection + SDK CSVs in out/ (no workspace, no DB)
	$(PY) -m seed preview

up:                     ## provision Lakebase (from config) + seed (relational + kv_store)
	$(PY) -m seed up

seed:                   ## (re)seed both sellers: relational tables + kv_store + SDK CSVs
	$(PY) -m seed seed

kv:                     ## (re)materialize the SDK kv_store only (relational already loaded)
	$(PY) -m seed kv

reset:                  ## drop the aamp + kv_* schemas in all 3 DBs (keep instances)
	$(PY) -m seed reset

down:                   ## DELETE the 3 Lakebase instances (stops billing)
	$(PY) -m seed down --yes

PREFIX ?= aamp
PROFILE ?=                                       ## Databricks CLI profile (blank = DEFAULT/env)
DBX_PROFILE := $(if $(PROFILE),-p $(PROFILE),)
frontend:               ## build the seller cockpit + registry console + buyer console (vite) → */frontend/dist (Apps only pip-install)
	@cd seller/frontend && [ -d node_modules ] || NODE_OPTIONS= npm install --no-audit --no-fund
	cd seller/frontend && NODE_OPTIONS= npm run build
	@cd registry/frontend && [ -d node_modules ] || NODE_OPTIONS= npm install --no-audit --no-fund
	cd registry/frontend && NODE_OPTIONS= npm run build
	@cd buyer/frontend && [ -d node_modules ] || NODE_OPTIONS= npm ci --no-audit --no-fund
	cd buyer/frontend && NODE_OPTIONS= npm run build

render: frontend        ## build UI, then generate build/ app dirs (sellers + registry + buyer; unique source path each)
	$(PY) seller/render_apps.py $(PREFIX)
	$(PY) registry/render_apps.py $(PREFIX)
	$(PY) buyer/render_apps.py $(PREFIX) $(PROFILE)

deploy: render          ## render per-seller app dirs, DAB deploy, then tune endpoints (PREFIX=aamp [PROFILE=name])
	databricks bundle deploy $(DBX_PROFILE) --var="instance_prefix=$(PREFIX)"
	$(MAKE) endpoints PREFIX=$(PREFIX) PROFILE=$(PROFILE)

endpoints:              ## tune each project's auto-created primary endpoint to 0.5-1 CU (waits until ready; scale-to-zero already on)
	@for party in seller buyer registry; do \
	  ep="projects/$(PREFIX)-$$party/branches/production/endpoints/primary"; \
	  echo "→ $$ep  (0.5–1 CU)"; \
	  ok=0; \
	  for i in 1 2 3 4 5 6 7 8 9 10 11 12; do \
	    if databricks postgres update-endpoint $$ep \
	         "spec.autoscaling_limit_min_cu,spec.autoscaling_limit_max_cu" \
	         --json '{"spec": {"endpoint_type": "ENDPOINT_TYPE_READ_WRITE", "autoscaling_limit_min_cu": 0.5, "autoscaling_limit_max_cu": 1}}' \
	         $(DBX_PROFILE) >/dev/null 2>&1; then ok=1; break; \
	    else echo "   …$$party endpoint not ready (attempt $$i), waiting 15s"; sleep 15; fi; \
	  done; \
	  [ $$ok -eq 1 ] || { echo "ERROR: $$party endpoint never accepted CU update"; exit 1; }; \
	done
	@echo "  (scale-to-zero is on by default — endpoints suspend when idle; idle timeout is the"
	@echo "   project default, fixed at create time and not updatable post-create.)"

grant-apps:             ## register each app's service principal as a Postgres role (run after deploy)
	$(PY) seller/grant_apps.py $(PREFIX) $(PROFILE)
	$(PY) registry/grant_apps.py $(PREFIX) $(PROFILE)
	$(PY) buyer/grant_apps.py $(PREFIX) $(PROFILE)

grant-app-access:       ## grant the buyer app's SP CAN_USE on the registry + seller apps (app-to-app MCP through the proxy)
	$(PY) grant_app_access.py $(PREFIX) $(PROFILE)

run-apps:               ## deploy app code + start each app (like `bundle run` for a job)
	databricks bundle run seller_summit_app $(DBX_PROFILE) --var="instance_prefix=$(PREFIX)"
	databricks bundle run seller_beacon_app $(DBX_PROFILE) --var="instance_prefix=$(PREFIX)"
	databricks bundle run registry_app $(DBX_PROFILE) --var="instance_prefix=$(PREFIX)"
	databricks bundle run buyer_app $(DBX_PROFILE) --var="instance_prefix=$(PREFIX)"

register-sellers:       ## register the live seller apps into the registry (dynamic; needs apps running + seeded)
	$(PY) registry/register_sellers.py $(PREFIX) $(PROFILE)

wire-buyer:             ## bake the deployed registry URL into the buyer + redeploy it (registry must already exist)
	$(PY) buyer/render_apps.py $(PREFIX) $(PROFILE)
	databricks bundle deploy $(DBX_PROFILE) --var="instance_prefix=$(PREFIX)"
	databricks bundle run buyer_app $(DBX_PROFILE) --var="instance_prefix=$(PREFIX)"

seed-job:               ## run the deployed seed_job (relational + kv_store + registry kv) on the deployed instances
	databricks bundle run seed_job $(DBX_PROFILE) --var="instance_prefix=$(PREFIX)"

all: deploy grant-apps grant-app-access seed-job run-apps register-sellers wire-buyer   ## full install: infra → grants (db + serving + app-to-app) → seed → start apps → register sellers → wire buyer to live registry
	@echo "✓ AAMP stack up (2 sellers + registry + buyer). Apps:"
	@echo "  $(PREFIX)-seller-summit / $(PREFIX)-seller-beacon / $(PREFIX)-registry-app / $(PREFIX)-buyer-app"
	@echo "  (see 'databricks apps get <name>' for URLs)"
	@echo "  Buyer is wired to the live registry: it discovers sellers by their card's"
	@echo "  inventory_types. The presented identity tier is chosen per-run in the buyer UI."
	@echo "  'make grant-apps' auto-grants the buyer SP: CAN_QUERY on custom/provisioned Model"
	@echo "  Serving endpoints (AAMP_LLM_ENDPOINTS=\"ep1,ep2\" to override; the default"
	@echo "  Databricks-hosted foundation-model endpoint is workspace-governed, SP inherits)."
	@echo "  'make grant-app-access' grants CAN_USE on the registry + seller apps so the"
	@echo "  buyer SP can call them through the OAuth proxy."

clean:                  ## remove local generated CSVs
	rm -rf out/
