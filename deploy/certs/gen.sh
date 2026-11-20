#!/bin/sh
# Demo PKI for TrustLine. Runs in the alpine `certgen` container or in Git Bash.
#   out/ca/{ca.crt,ca.key}            root CA (key mounted ONLY into control)
#   out/ca.crt                        CA cert distributed to every party
#   out/servers/<name>/server.{crt,key}
#   out/collectors/<id>/client.{crt,key}   subject CN=<id>, O=tenant-<tenant>
#   out/rogue/...                     a rogue CA + collector claiming tenant-acme (must FAIL)
set -eu
# EXTRA_SAN="DNS:bore.pub" adds names to the server certs (Colab tunnel, see deploy/colab/README.md)
export MSYS_NO_PATHCONV=1   # Git Bash: stop "/CN=..." being rewritten to a Windows path
cd "$(dirname "$0")"
OUT=out
DAYS=30
if [ -f "$OUT/ca/ca.crt" ] && [ "${FORCE:-0}" != "1" ]; then
  echo "certs exist (FORCE=1 to regenerate)"; exit 0
fi
command -v openssl >/dev/null 2>&1 || apk add --no-cache openssl >/dev/null
rm -rf "$OUT"
mkdir -p "$OUT/ca" "$OUT/servers" "$OUT/collectors" "$OUT/rogue"
EC="-newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes"

openssl req -x509 $EC -keyout "$OUT/ca/ca.key" -out "$OUT/ca/ca.crt" -days $DAYS \
  -subj "/CN=TrustLine Demo Root CA/O=trustline" \
  -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null
cp "$OUT/ca/ca.crt" "$OUT/ca.crt"

# issue <dir> <subject> <eku> <basename> [san] [ca-dir]
issue() {
  d=$1; subj=$2; eku=$3; base=$4; san=${5:-}; cadir=${6:-$OUT/ca}
  mkdir -p "$d"
  openssl req $EC -keyout "$d/$base.key" -out "$d/req.csr" -subj "$subj" 2>/dev/null
  {
    echo "basicConstraints=CA:FALSE"
    echo "keyUsage=critical,digitalSignature"
    echo "extendedKeyUsage=$eku"
    if [ -n "$san" ]; then echo "subjectAltName=$san"; fi
  } > "$d/ext.cnf"
  openssl x509 -req -in "$d/req.csr" -CA "$cadir/ca.crt" -CAkey "$cadir/ca.key" -CAcreateserial \
    -days $DAYS -sha256 -extfile "$d/ext.cnf" -out "$d/$base.crt" 2>/dev/null
  rm -f "$d/req.csr" "$d/ext.cnf"
}

for s in nginx-ap nginx-eu control; do
  issue "$OUT/servers/$s" "/CN=$s/O=trustline" serverAuth server "DNS:$s,DNS:localhost,IP:127.0.0.1${EXTRA_SAN:+,$EXTRA_SAN}"
done
for c in "col-ac01:acme" "col-9b01:globex"; do
  id=${c%%:*}; t=${c##*:}
  issue "$OUT/collectors/$id" "/CN=$id/O=tenant-$t" clientAuth client
done

# Rogue CA + rogue collector claiming tenant-acme: must fail the TLS handshake at nginx.
openssl req -x509 $EC -keyout "$OUT/rogue/ca.key" -out "$OUT/rogue/ca.crt" -days $DAYS \
  -subj "/CN=Rogue CA/O=evil" \
  -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign" 2>/dev/null
issue "$OUT/rogue/client" "/CN=col-ac01/O=tenant-acme" clientAuth client "" "$OUT/rogue"

chmod -R a+r "$OUT"
echo "PKI written to deploy/certs/out"
