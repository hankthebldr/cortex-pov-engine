# Authentication

> Verified against `ebarti/cortex-xdr-client` (`api/authentication.py`, `base_api.py`) and
> PANW LIVEcommunity advanced-auth threads. The **Advanced** signature construction below is
> the one the harness must implement.

Every Platform API request authenticates with an **API key** plus its numeric **API key
ID**. There are two key classes, chosen when the key is minted in the console
(**Settings → Configurations → Integrations → API Keys → New Key**):

| Class | Header work per request | Replay protection | Recommendation |
|-------|-------------------------|-------------------|----------------|
| **Standard** | Send the raw key | None | Fine for lab/POC; simplest |
| **Advanced** | Compute a per-request SHA-256 signature over key + nonce + timestamp | Yes (nonce + timestamp) | **Preferred** — use for the harness |

Both classes carry an RBAC **role** chosen at mint time. Give the harness key a
**least-privilege, read-only** role (see §4).

---

## 1. Standard authentication

Two headers:

| Header | Value |
|--------|-------|
| `x-xdr-auth-id` | The API key **ID** (an integer, e.g. `3`) |
| `Authorization` | The API key string, verbatim |
| `Content-Type` | `application/json` |

```bash
curl -sS -X POST \
  "https://api-${FQDN}/public_api/v1/incidents/get_incidents/" \
  -H "x-xdr-auth-id: ${API_KEY_ID}" \
  -H "Authorization: ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"request_data": {}}'
```

---

## 2. Advanced authentication

Four headers. The `Authorization` header is **not** the raw key — it is a hex SHA-256
digest that changes every request.

| Header | Value |
|--------|-------|
| `x-xdr-auth-id` | The API key **ID** (integer) |
| `x-xdr-nonce` | A fresh random 64-char alphanumeric string, unique per request |
| `x-xdr-timestamp` | Current UTC time in **milliseconds** since epoch, as a string |
| `Authorization` | `sha256_hex( api_key + nonce + timestamp )` |
| `Content-Type` | `application/json` |

**Signature algorithm** (the string is the concatenation of the three values, in this
order, with no separators):

```
auth_key      = api_key + nonce + timestamp     # string concatenation
Authorization = hex( sha256( utf8(auth_key) ) )
```

### Reference implementation (Python — matches the harness)

```python
import hashlib, secrets, string, time

def advanced_headers(api_key: str, api_key_id: int) -> dict:
    nonce = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(64))
    timestamp = str(int(time.time() * 1000))          # epoch milliseconds
    auth_key = f"{api_key}{nonce}{timestamp}".encode("utf-8")
    signature = hashlib.sha256(auth_key).hexdigest()
    return {
        "x-xdr-auth-id":  str(api_key_id),
        "x-xdr-nonce":    nonce,
        "x-xdr-timestamp": timestamp,
        "Authorization":  signature,
        "Content-Type":   "application/json",
    }
```

### curl (bash) equivalent

```bash
NONCE=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' | head -c 64)
TS=$(( $(date +%s) * 1000 ))
SIG=$(printf '%s%s%s' "$API_KEY" "$NONCE" "$TS" | sha256sum | cut -d' ' -f1)

curl -sS -X POST \
  "https://api-${FQDN}/public_api/v1/xql/get_quota/" \
  -H "x-xdr-auth-id: ${API_KEY_ID}" \
  -H "x-xdr-nonce: ${NONCE}" \
  -H "x-xdr-timestamp: ${TS}" \
  -H "Authorization: ${SIG}" \
  -H "Content-Type: application/json" \
  -d '{"request_data": {}}'
```

**Clock skew:** the timestamp is validated server-side. Keep the harness host within a few
minutes of true UTC (NTP). Excess skew → `401`.

---

## 3. Common auth failures

| Symptom | Likely cause |
|---------|--------------|
| `401 Unauthorized` | Wrong `x-xdr-auth-id`, wrong signature construction, stale timestamp, or a standard key sent with advanced headers (or vice-versa) |
| `403 Forbidden` | Key's **role** lacks permission for that `api_name`/`call_name` |
| `402 Payment Required` | Capability not licensed on the tenant (e.g. XQL, host insights) |
| Works in Postman, fails in code | Concatenation order or encoding of the advanced signature (must be `key + nonce + timestamp`, UTF-8, lowercase hex) |

---

## 4. RBAC scoping for the harness key

API keys inherit a **role**; the harness should use a **custom read-only role** so a leaked
key cannot isolate hosts or run scripts. Minimum grants for the read/pull surface:

- **Incidents / Alerts:** View
- **XQL / Query Center:** Run queries (+ dataset read on `metrics_*` for health)
- **Endpoints:** View (no Action / Response permissions)
- **Audit logs:** View
- **Health:** View

Do **not** grant Response Actions, Agent management, Blocklist/Allowlist edit, or
Settings/Admin. The harness never needs them and `CLAUDE.md` forbids write-back.

---

## 5. Credential handling in CortexSim

The key + key ID live only in the encrypted vault (`/api/credentials/integrations`, master
key `CORTEXSIM_MASTER_KEY`) — never in scenario YAML, git, logs, or push bundles. The
harness reads them at call time and injects the headers above. See
[`99-harness-design-notes.md`](99-harness-design-notes.md).
