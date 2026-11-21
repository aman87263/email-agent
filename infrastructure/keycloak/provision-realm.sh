#!/bin/bash
# Keycloak realm provisioning script for Email Agent
# Run against the shared Keycloak instance on the OCI VM
# Idempotent - safe to re-run

set -euo pipefail

KC_ADMIN_USER="${KC_ADMIN_USER:-admin}"
KC_ADMIN_PASSWORD="${KC_ADMIN_PASSWORD:-}"
KC_URL="${KC_URL:-http://localhost:8080}"
REALM_NAME="email-agent"
CLIENT_ID="email-agent-bff"
REDIRECT_URI="https://capriman27.duckdns.org/mail/api/auth/callback"
FRONTEND_ORIGIN="https://capriman27.duckdns.org"
FRONTEND_URL="https://capriman27.duckdns.org/mail/"

if [[ -z "$KC_ADMIN_PASSWORD" ]]; then
    echo "ERROR: KC_ADMIN_PASSWORD must be set"
    exit 1
fi

echo "Provisioning Keycloak realm: $REALM_NAME"

# Login to Keycloak
/opt/keycloak/bin/kcadm.sh config credentials \
    --server "$KC_URL" \
    --realm master \
    --user "$KC_ADMIN_USER" \
    --password "$KC_ADMIN_PASSWORD"

# Create realm if it doesn't exist
if /opt/keycloak/bin/kcadm.sh get realms/"$REALM_NAME" >/dev/null 2>&1; then
    echo "Realm $REALM_NAME already exists"
else
    echo "Creating realm $REALM_NAME"
    /opt/keycloak/bin/kcadm.sh create realms -s realm="$REALM_NAME" -s enabled=true -s displayName="Email Agent"
fi

# Create confidential client
CLIENT_UUID=$(/opt/keycloak/bin/kcadm.sh get clients -r "$REALM_NAME" -q clientId="$CLIENT_ID" --fields id | jq -r '.[0].id // empty')

if [[ -z "$CLIENT_UUID" ]]; then
    echo "Creating client $CLIENT_ID"
    CLIENT_UUID=$(/opt/keycloak/bin/kcadm.sh create clients -r "$REALM_NAME" \
        -s clientId="$CLIENT_ID" \
        -s name="Email Agent BFF" \
        -s enabled=true \
        -s "redirectUris=[\"$REDIRECT_URI\"]" \
        -s "webOrigins=[\"$FRONTEND_ORIGIN\"]" \
        -s protocol=openid-connect \
        -s publicClient=false \
        -s directAccessGrantsEnabled=false \
        -s standardFlowEnabled=true \
        -s implicitFlowEnabled=false \
        -s serviceAccountsEnabled=false \
        -s authorizationServicesEnabled=false \
        -i)
    echo "Created client with UUID: $CLIENT_UUID"
else
    echo "Client $CLIENT_ID already exists (UUID: $CLIENT_UUID)"
    # Update redirect URIs and web origins
    /opt/keycloak/bin/kcadm.sh update clients/"$CLIENT_UUID" -r "$REALM_NAME" \
        -s "redirectUris=[\"$REDIRECT_URI\"]" \
        -s "webOrigins=[\"$FRONTEND_ORIGIN\"]" \
        -s directAccessGrantsEnabled=false
fi

# Get client secret — written to a 0600 file, never echoed to stdout/logs
SECRET_FILE="${SECRET_OUT:-/opt/email-agent/.keycloak-client-secret}"
CLIENT_SECRET=$(/opt/keycloak/bin/kcadm.sh get clients/"$CLIENT_UUID"/client-secret -r "$REALM_NAME" | jq -r '.value')

umask 077
printf 'KEYCLOAK_CLIENT_SECRET=%s\n' "$CLIENT_SECRET" > "$SECRET_FILE"
chmod 600 "$SECRET_FILE"

# Add Audience protocol mapper
MAPPER_EXISTS=$(/opt/keycloak/bin/kcadm.sh get clients/"$CLIENT_UUID"/protocol-mappers/models -r "$REALM_NAME" -q name=audience-mapper --fields id | jq -r '.[0].id // empty')

if [[ -z "$MAPPER_EXISTS" ]]; then
    echo "Adding Audience protocol mapper"
    /opt/keycloak/bin/kcadm.sh create clients/"$CLIENT_UUID"/protocol-mappers/models -r "$REALM_NAME" \
        -s name="audience-mapper" \
        -s protocol=openid-connect \
        -s protocolMapper=oidc-audience-mapper \
        -s 'config."included.client.audience"=email-agent-api' \
        -s 'config."access.token.claim"=true' \
        -s 'config."id.token.claim"=false'
else
    echo "Audience protocol mapper already exists"
fi

# Create realm role
ROLE_EXISTS=$(/opt/keycloak/bin/kcadm.sh get roles -r "$REALM_NAME" -q name=email-agent-user --fields id | jq -r '.[0].id // empty')

if [[ -z "$ROLE_EXISTS" ]]; then
    echo "Creating realm role email-agent-user"
    /opt/keycloak/bin/kcadm.sh create roles -r "$REALM_NAME" -s name=email-agent-user -s description="Email Agent user"
else
    echo "Role email-agent-user already exists"
fi

# This is a dedicated realm. Make the application role a composite of the
# realm's default role so newly created local users can log in immediately.
# kcadm's add-roles operation is idempotent when the composite already exists.
DEFAULT_ROLE="default-roles-$REALM_NAME"
/opt/keycloak/bin/kcadm.sh add-roles -r "$REALM_NAME" \
    --rname "$DEFAULT_ROLE" \
    --rolename "email-agent-user"

echo ""
echo "=== Provisioning Complete ==="
echo "Realm: $REALM_NAME"
echo "Client ID: $CLIENT_ID"
echo "Client secret: written to $SECRET_FILE (mode 600) — not printed to stdout"
echo "Redirect URI: $REDIRECT_URI"
echo ""
echo "Copy the secret into your .env file:"
echo "  KEYCLOAK_CLIENT_SECRET=\$(grep -oP '(?<=KEYCLOAK_CLIENT_SECRET=).*' $SECRET_FILE)"
echo ""
echo "Register this Google OAuth redirect URI in Google Cloud Console:"
echo "https://capriman27.duckdns.org/mail/api/v1/connections/gmail/callback"
