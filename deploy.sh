#!/usr/bin/env bash
# deploy.sh — Deploy or update the News Aggregator infrastructure + function code.
#
# Usage:
#   ./deploy.sh              # Deploy/update everything
#   ./deploy.sh --infra-only # Only deploy Bicep infrastructure
#   ./deploy.sh --code-only  # Only deploy function code
#
# Prerequisites:
#   - Azure CLI logged in (az login)
#   - Azure Functions Core Tools v4 (func)
#   - Python 3.11+ with venv

set -euo pipefail
cd "$(dirname "$0")/.."

RG="rg-news-aggregator"
LOCATION="southafricanorth"
FUNC_APP="func-newsaggregator"
BICEP_FILE="infra/main.bicep"

echo "=== News Aggregator Azure — Deploy ==="

# ── Infrastructure ──────────────────────────────────────────────────────────

deploy_infra() {
    echo ""
    echo ">>> Deploying infrastructure (Bicep)..."

    # Ensure resource group exists
    az group create --name "$RG" --location "$LOCATION" --output none 2>/dev/null || true

    # Deploy Bicep — idempotent, will update in-place if resources exist
    az deployment group create \
        --resource-group "$RG" \
        --template-file "$BICEP_FILE" \
        --parameters appName=newsaggregator \
        --output table

    # Show outputs
    echo ""
    echo "--- Deployment outputs ---"
    az deployment group show \
        --resource-group "$RG" \
        --name "$(az deployment group list --resource-group "$RG" --query "[0].name" -o tsv)" \
        --query "properties.outputs" -o json 2>/dev/null | python3 -m json.tool || true

    echo "Infrastructure deployed."
}

# ── Function code ───────────────────────────────────────────────────────────

deploy_code() {
    echo ""
    echo ">>> Deploying function code..."

    # Ensure venv is active
    if [ ! -d ".venv" ]; then
        python3.11 -m venv .venv
    fi
    source .venv/bin/activate
    pip install -q -r requirements.txt

    # Publish to Azure
    func azure functionapp publish "$FUNC_APP" --python

    echo ""
    echo "--- Verifying health endpoint ---"
    sleep 5  # allow cold start
    HEALTH_URL="https://${FUNC_APP}.azurewebsites.net/api/health"

    for i in 1 2 3 4 5; do
        STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$HEALTH_URL" 2>/dev/null || echo "000")
        echo "  Attempt $i: HTTP $STATUS"
        if [ "$STATUS" = "200" ]; then
            echo ""
            echo "✅ Function App is running"
            curl -s "$HEALTH_URL" | python3 -m json.tool
            return 0
        fi
        sleep 5
    done
    echo "⚠️  Health check returned non-200 after 5 attempts"
    return 1
}

# ── Main ────────────────────────────────────────────────────────────────────

case "${1:-}" in
    --infra-only)
        deploy_infra
        ;;
    --code-only)
        deploy_code
        ;;
    *)
        deploy_infra
        deploy_code
        ;;
esac

echo ""
echo "=== Deploy complete ==="
