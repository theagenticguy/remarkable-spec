# reMarkable Paper Pro device specification — firmware 3.27.3.0

Measured, evidence-carrying description of what a reMarkable Paper Pro running
firmware **3.27.3.0** actually exposes: its USB-C HTTP API, its system D-Bus
surface, the on-disk layout of the xochitl data directory, its systemd unit
graph, and the logger categories that make all of the above observable.

Every entry in every file carries the command that produced it and that
command's output. **A spec entry without evidence is not admissible** — if you
add a claim, add its evidence string.

Every file also carries a `refuted` section listing claims that did *not*
survive verification, with the observation that killed them. That section is
the most useful part of this spec. It records the specific ways a plausible
reading of this device turns out to be wrong.

---

## Files

| File | Contents |
|---|---|
| `http.json` | Route table for the USB-C web server on `10.11.99.1:80` — path templates, methods that work, status codes, Content-Type advertised vs actual, response shapes, the full error contract |
| `dbus.json` | System bus: service → object path → interface → methods / signals / properties, plus the bus policy matrix and the three flavours of the standard interface trio |
| `filesystem.json` | `/home/root/.local/share/remarkable/xochitl` — per-extension shape, which files a document always has vs optionally, including `.local`, `.failure`, `.tree` and the SQLite search index |
| `systemd.json` | Unit census with type and state, plus the edges that matter: what xochitl depends on, how SSH is socket-activated and gated, the encrypted-home boot chain |
| `subsystems.json` | The 49 `rm.*` / `xofm.*` logger categories — what each governs, its source file, its message shapes, and where a naive parse of the log goes wrong |

Common top-level keys in every JSON file:

```json
{
  "firmware": "3.27.3.0",
  "measured_on_kernel": "6.12.49+git-imx8mm-ferrari-g68b95e858a0a",
  "source": "read-only probe",
  "measured_at": "2026-08-28T23:00:00Z",
  "claims":  [ /* each entry carries an `evidence` string */ ],
  "refuted": [ /* each entry carries `claim`, `observation`, `correction` */ ]
}
```

---

## The device this describes

Re-verified at the start of the session, from `/etc/os-release`, `/etc/version`
and `/sys/devices/soc0/machine` only:

```
$ ssh remarkable 'cat /sys/devices/soc0/machine; cat /etc/version; uname -r; \
                  grep -E "^(NAME|VERSION|IMG_VERSION|BUILD_MODE_RM|ID)=" /etc/os-release'
reMarkable Ferrari
20260612085811
6.12.49+git-imx8mm-ferrari-g68b95e858a0a
ID=codex
NAME="Codex Linux"
VERSION="5.7.126 (scarthgap)"
BUILD_MODE_RM="public"
IMG_VERSION="3.27.3.0"
```

- **Hardware** — reMarkable Ferrari, i.MX8MM, aarch64. Display 1620×2160.
  Touch panel "Elan touch input" on `/dev/input/event3`, raw range 0–2064 × 0–2832,
  reporting **no** pressure.
- **OS** — Codex Linux 5.7.126 (scarthgap), OpenEmbedded, build `20260612085811`,
  `BUILD_MODE_RM=public`.
- **Userland** — BusyBox 1.36.1. **No GNU long options.** `head -25` fails; use
  `sed -n 1,25p`. `file`, `sqlite3` and `python3` are **not** installed.
  `od`, `hexdump`, `xxd`, `strings`, `dd`, `find`, `md5sum` are.
- **Root filesystem** is read-only ext4. `/etc` is a volatile overlay.
  `/home` is dm-crypt (`/dev/mapper/home-encrypted-disk`).
- **Reachability** — `10.11.99.1` over the USB-C gadget, host side `10.11.99.12`.
  Ports **22** and **80** only. The `10.11.99.1/27` address lives on `usb1`
  (CDC ECM); `usb0` (RNDIS) has no address.

`BUILD_MODE_RM=public` matters more than it looks. On this build the OTA D-Bus
service `no.remarkable.update1` is backed by a binary literally named
`/usr/bin/fakeupdateengine_service`, with unit description "Fake Update Engine".
Anything treating that interface as a live OTA channel here is talking to a mock.

---

## The read-only constraint

This spec was produced under a binding read-only rule, and the rule shaped the
method:

- **HTTP: `GET` and `HEAD` only.** No `POST`/`PUT`/`DELETE`/`PATCH`.
- **`POST /upload` was never requested in any form, including `GET`.** The Qt
  server **ignores the request method** — the path alone selects the handler.
  So a `GET` to `/upload` is indistinguishable from a `POST` to it, and cannot
  be proven non-mutating. It is documented in `http.json` from the log oracle
  and the shipped web-UI bundle, and explicitly flagged `not_probed: true`.
- **D-Bus: introspection only.** No method invoked, no property written. Two
  read-only exceptions are disclosed inline in `dbus.json` rather than buried.
- **SSH: reads only.** `ls`, `cat`, `sed`, `grep`, `wc`, `md5sum`, `strings`,
  `xxd`, `dd`, `du`, `find`, `mount`, `systemctl show`, `busctl introspect`,
  `journalctl`.
- **No service restarted, no unit enabled or disabled, no setting changed.**
- **`/home/root/.config/remarkable/xochitl.conf` was never opened.** It holds a
  cleartext `DeveloperPassword` and two JWTs. Identity in this spec comes only
  from `/etc/os-release`, `/etc/version` and `/sys/devices/soc0/machine`.

### Side effects that could not be avoided, and are stated anyway

1. **The journal.** Every `ssh` invocation appends dropbear and systemd records
   to the systemd journal, which is an ext4 mount on the *same encrypted
   partition* as `/home` and survives reboots. "Zero writes to persistent
   storage" is not achievable while reading over SSH. Say so; don't claim it away.
2. **tmpfs scratch.** One file, `/tmp/rmspec_u.txt` (the 42 entity UUIDs), used
   to drive the correlation loops. `/tmp` is tmpfs — volatile, outside the data
   dir. Not deleted, because deletion is equally forbidden.
3. **Activatable D-Bus names.** Introspecting an unowned but *activatable* bus
   name **starts the service**. `org.freedesktop.hostname1`, `locale1` and
   `timedate1` are activatable and were started by introspection in a prior
   session. This session did **not** re-introspect them; their shapes are carried
   forward with attribution. `org.bluez` was likewise not re-attempted.
4. **Data-dir cleanliness held.** 201 top-level entries at session start, 201 at
   session end.

---

## The `/log.txt` oracle — the technique that makes this cheap

`GET /log.txt` returns the persistent systemd journal (~9.7 MB, ~79,000 lines,
six retained boots). **Every request the Qt web server handled appears in it
with the server's own verdict**, under the logger category `rm.usb.web`. That
turns route discovery from guesswork about status codes into reading what the
server said about your request. No proxy and no MITM.

### The three verdicts

| Log output | Meaning |
|---|---|
| `Bad path "/whatever" ""` **then** `ERROR "Unknown file"` | The path is **not routed**. |
| A **bare** `ERROR "<message>"` with *no* preceding `Bad path` | The path **is routed**; the handler rejected your arguments. |
| **Nothing at all** | The path is routed and it **succeeded**. |

That third row is the one people miss. A successful `GET /documents/` logs
nothing, so silence is a positive result, not a failure to observe.

### How to use it: bracket the probe with sentinels

Request a path you know is nonsense before and after each probe batch. Each
sentinel produces a `Bad path` line with its own name in it, so you can slice
the log to exactly your window with `awk`:

```bash
curl -sS -o /dev/null http://10.11.99.1/RMSPEC-PROBE-START

# ... the probes you actually care about ...
curl -sS -o /dev/null -w '%{http_code} %{content_type} %{size_download}\n' \
  http://10.11.99.1/download/<uuid>/pdf

curl -sS -o /dev/null http://10.11.99.1/RMSPEC-PROBE-END

curl -sS -o /tmp/log.txt http://10.11.99.1/log.txt
awk '/RMSPEC-PROBE-START/,0' /tmp/log.txt | grep -a 'rm.usb.web' \
  | sed 's/^.*rm\.usb\.web *//'
```

This is exactly how `/download/{id}/{format}` was pinned down. Probing seven
values of the third path segment produced five bare
`ERROR "Filetype not supported"` lines and two silences — and the two silences
were `pdf` and `rmdoc`, which is the whole answer.

### What the oracle cannot do

- **It is redaction-filtered.** `/log.txt` is journal-*derived*, not the journal
  byte-for-byte. 407 lines replace MAC / BSSID / SSID values with the literal
  `<redacted>`, versus **0** such lines in `journalctl` output — and in 223 of
  them the redaction **swallows the rest of the line**. Matched values are
  unrecoverable from `/log.txt`. The oracle itself is unaffected: 0 of the 174
  `rm.usb.web` lines are redacted.
- **The window rotates.** `journald.conf` sets `SystemMaxUse=50M` with 47.5 M
  already in use. The oldest boots are being discarded continuously. **Every
  line count in this spec is a timestamped observation, never a firmware
  constant.** Several categories emit a fixed number of lines *per boot*, so
  their counts move in lockstep with retained boots.
- **`journalctl --list-boots | wc -l` overcounts by one.** systemd 255 prints a
  header row it will not suppress here (`--no-legend` is unrecognised for that
  subcommand).
- **The category column is 25 characters wide, and names ≥25 chars run into
  their message.** A whitespace-splitting parser reports 57 raw category tokens
  for what are really 47. Nine collision groups produce 13 phantom names —
  `rm.epaperevdevtouchscreenhandler` alone produces nine. The map is in
  `subsystems.json`.
- **`rm.tmp` is not a logger category.** It matches 255 times, always as the
  `.rm.tmp` *file suffix* inside `rm.scenefile` messages.

---

## How to regenerate this spec

1. **Verify reachability first, and stop if the tablet is asleep.** This is not
   ceremony: a prior session lost the device mid-run and several claims could
   only be re-checked against cached artifacts.

   ```bash
   ifconfig en11 | grep -E 'inet |status'
   ping -c 2 -t 3 10.11.99.1
   ssh -o ConnectTimeout=8 -o BatchMode=yes remarkable \
     'echo SSH_OK; cat /sys/devices/soc0/machine; cat /etc/version; uname -r'
   ```

   If `en11` is gone or ports 22/80 time out, **stop and say so.** Do not
   silently fall back to a cached `/log.txt` and present it as current.

2. **`http.json`** — probe each route family with `curl`, bracketed by sentinels,
   then read the log slice. Confirm both the status code *and* the log verdict.
   Sniff response magic bytes: `/thumbnail/{id}` advertises `image/jpeg` and
   returns PNG. Cross-check against the shipped web UI, which is the authoritative
   client-side route list:

   ```bash
   ssh remarkable 'grep -o -E "/(documents|download|upload|thumbnail|log)[a-zA-Z0-9_./{}$?=&-]*" \
     /usr/share/remarkable/webui/assets/index.js | sort | uniq -c'
   ```

   Never touch `/upload`.

3. **`dbus.json`** — `busctl --system list`, `busctl --system tree <name>`, then
   `busctl --system introspect <name> <path> --xml-interface --no-pager`.
   **Parse the XML properly.** QtDBus emits whole interfaces on one line, so
   `grep -c '<property'` undercounts; use an XML parser. Check `busctl list`
   for `pid=-` before introspecting a name — an unowned activatable name will be
   *started* by your probe.

4. **`filesystem.json`** — enumerate the 42 entity UUIDs from `*.metadata`, then
   drive one correlation loop that tests every sidecar per UUID and histograms
   the signatures. Read the `.db` header **on device** with `dd | xxd` and
   `dd bs=4096 count=3 | strings | grep CREATE` rather than copying user
   handwriting to the host.

5. **`systemd.json`** — `systemctl show` per unit, **and always read
   `DropInPaths`**. Reading only `FragmentPath` produced a materially wrong
   `xochitl.service` in a prior pass. Strip the `●`/`○` marker glyph before any
   `awk` field extraction. Discount transient `dropbear@*` instances from
   totals — your own SSH sessions create them.

6. **`subsystems.json`** — one `GET /log.txt`, then two extractions reconciled:
   the xochitl 25-column padded format and the `category: msg` format used by
   every other daemon. Collapse padding collisions using the source-file path
   each line carries. Re-derive counts; do not copy them forward.

---

## Reading rules that were learned the hard way

Each of these cost a refuted claim. They are in the `refuted` sections with full
observations; this is the short list.

- **A grep that only ever matches failures proves nothing about success rates.**
  `rm.http.transaction` logs *only* failures, so grepping it yields a 100 %-failure
  sample whether or not the cloud works. Outcome-logging siblings showed the same
  endpoints succeeding.
- **A single introspect of one object does not establish a universal.** "The
  standard trio is on every object" and "these are the only writable properties
  on the device" both died to objects the original probe never visited.
- **Field names matter: `Requires=` is not `RequiredBy=`, and `After=` is
  neither.** Three dependency claims inverted direction or promoted ordering to
  requirement. `After=` is ordering only; a unit ordered after a failed mount
  still starts.
- **Path components can be format selectors, not names.** `/download/{id}/{name}`
  is really `/download/{id}/{format}` with values `pdf` and `rmdoc`. The
  filename-shaped guess returned `400` for every value tried.
- **D-Bus link object paths track live ifindex.** `/link/_36` was wlan0; wlan0 is
  now ifindex 8 and the object is `/link/_38`. Only the *escaping rule* is
  firmware-invariant. Resolve paths from `/sys/class/net/*/ifindex` at runtime.
- **A flat any-depth key grep is not a schema.** `.content` has 22–24 *top-level*
  keys; an any-depth distinct-key count on the same file is 71. A digit-blind
  pattern also silently drops 21 real keys.
- **`inactive/dead` on a oneshot-style unit is ambiguous.** Read
  `ExecMainStatus`, `ExecMainStartTimestamp` and `ConditionResult` before
  concluding a unit was skipped. One "a condition gate skipped it" claim died to
  `ConditionResult=yes` and a 173 ms successful run.
- **Zero-length files exist and will crash your parser.** 86 of 194 `.rm` files
  are exactly 0 bytes; `rmscene` raises `EOFError`. Stat and skip.
- **Counts drift; names, source files and message shapes do not.** Prefer the
  latter in any assertion you intend to keep.

---

## Provenance

- Measured 2026-08-28, ~22:56–23:05 UTC, against the live device over USB-C.
- Where a claim could only be re-verified from a prior session — the three
  activatable systemd D-Bus services, `org.bluez`, and the search-index row
  counts — it is marked with attribution inline. Everything else in the
  `claims` arrays was re-measured in this session.
- Redaction: the `.tree` cloud account identifier (a Google/Auth0 OAuth2 subject
  id) and the device serial from `rm.user.auth.cli` are **not** reproduced. No
  token, PSK, or key material appears in any file here.
