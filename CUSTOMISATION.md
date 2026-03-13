# Customising the Demo

The demo is designed to be reskinned for different customers and use cases in under an hour.
The stack — Gravitee APIM, AM, A2A agent, MCP backend — stays identical.
You're swapping the frontend and pointing it at a different agent/dataset.

---

## The 5 things you change

### 1. Frontend skin (`rnli-website/`)

| File | What to change |
|---|---|
| `index.html` | Logo, brand name, hero copy, colour scheme |
| `styles.css` | CSS variables at the top of the file (`--rnli-red`, `--rnli-orange`, etc.) |
| `assets/` | Replace logos, hero images |
| `script.js` line 25 | `GUARD_RAILS_TEST_PHRASE` — the harmful query used in the demo |

The Gravitee colour scheme for reference: primary `#6750A4` (purple), accent `#D6409F` (pink).

### 2. Agent endpoint

The website discovers the agent by fetching the A2A agent card URL.
Override it at runtime without rebuilding — set env vars in `docker-compose.yml`:

```yaml
rnli-website:
  environment:
    - AGENT_CARD_URL=http://localhost:8082/your-agent/.well-known/agent-card.json
    - OIDC_URL=http://localhost:8092/your-domain/oidc/.well-known/openid-configuration
    - OIDC_CLIENT_ID=your-client-id
    - REDIRECT_URI=http://localhost:8002/
```

The entrypoint script (`rnli-website/docker-entrypoint.d/40-env-config.sh`) generates
`config.js` from these values at container start. Defaults are `localhost` — no change
needed for a standard local setup.

### 3. AM users and plans

Edit `gravitee-init/am-apps/rnli-lifeboat.yaml` to change the OAuth app name/clientId.

Users are defined in `gravitee-init/am_init.py` — look for the `USER_*` and `SILVER_USER_*`
constants at the top of the file. Change names, emails and passwords there.

Plan names are cosmetic — they're just `additionalInformation.plan` values on the AM user.
Change them in `am_init.py` and update the matching labels in `rnli-website/script.js`
(search for `'gold'` and `'silver'`).

### 4. Guard Rails sensitivity

The DistilBERT toxicity classifier threshold is set in `gravitee-init/apim-apis/03-llm-proxy.json`
(`sensitivityThreshold: 0.5`).

Override without editing the JSON — set in `docker-compose.yml` on `gio-gravitee-init`:

```yaml
gio-gravitee-init:
  environment:
    - GUARD_RAILS_THRESHOLD=0.6   # higher = less sensitive
```

Range: `0.0` (block almost everything) to `1.0` (block almost nothing).
`0.5` is a good default for demo use. Drop to `0.3` if you want the demo query to hit
the threshold more reliably; raise to `0.7` if you're getting false positives.

### 5. Agent Inspector demo scenarios (`agent-live-graph/`)

Each demo scenario is a plain array of step objects in
`agent-live-graph/public/app.js` — `SCENARIO`, `SCENARIO_GUARD_RAILS`,
`SCENARIO_CACHE_HIT`, `SCENARIO_RATE_LIMIT`.

To add a customer-specific scenario:
1. Add a new `const SCENARIO_MYNAME = [ ... ]` array following the same pattern
2. Add a case to `getActiveScenario()` and `showDemoWaiting()`
3. Add an `<option>` to the `#scenarioSelect` dropdown in `index.html`

---

## Rebuilding after changes

The website and agent inspector are static nginx containers — you must rebuild after
any file changes:

```bash
docker compose build rnli-website agent-live-graph
docker compose up -d rnli-website agent-live-graph
```

The `gravitee-init` container reads env vars at runtime, so Guard Rails threshold
changes take effect on the next `docker compose up -d --force-recreate gio-gravitee-init`.

---

## Pointing at a different A2A agent

The agent itself (`rnli-a2a-agent/`) is a Python A2A server. It's the only
component with RNLI-specific logic (the lifeboat MCP tool calls and the system prompt).

To demo with a customer's own agent:
1. Point `AGENT_CARD_URL` at their agent's `/.well-known/agent-card.json`
2. Update the demo scenarios in `app.js` to reflect that agent's actual flow
3. The gateway, AM, guard rails, and cache policies are completely unchanged

If the customer doesn't have an A2A agent yet, the existing agent can be repurposed
by changing the system prompt in `rnli-a2a-agent/agent.py` and swapping the MCP
backend (`lifeboat-api/`) for one that wraps their data.
