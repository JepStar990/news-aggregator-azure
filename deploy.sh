#!/usr/bin/env bash
# deploy.sh — Deploy or update the News Aggregator infrastructure + function code.
#
# Usage:
#   ./deploy.sh              # Deploy/update everything
#   ./deploy.sh --infra-only # Only deploy Bicep infrastructure
#   ./deploy.sh --code-only  # Only deploy function code
#   ./deploy.sh --code-zip   # Deploy code via ZIP (fallback if func CLI OOMs)
#
# Prerequisites:
#   - Azure CLI logged in (az login)
#   - Azure Functions Core Tools v4 (func) — optional, use --code-zip if unavailable
#   - Python 3.11+ with venv

set -euo pipefail
cd "$(dirname "$0")"

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
    DEPLOY_NAME=$(az deployment group list --resource-group "$RG" --query "[0].name" -o tsv)
    az deployment group show \
        --resource-group "$RG" \
        --name "$DEPLOY_NAME" \
        --query "properties.outputs" -o json 2>/dev/null | python3 -m json.tool || true

    echo "Infrastructure deployed."
}

# ── Function code (func CLI) ────────────────────────────────────────────────

deploy_code() {
    echo ""
    echo ">>> Deploying function code (func CLI)..."

    # Ensure venv is active
    if [ ! -d ".venv" ]; then
        python3 -m venv .venv
    fi
    source .venv/bin/activate 2>/dev/null || true
    pip install -q -r requirements.txt 2>/dev/null || true

    # Publish to Azure
    func azure functionapp publish "$FUNC_APP" --python

    echo ""
    verify_health
}

# ── Function code (ZIP deploy via Azure CLI) ────────────────────────────────

deploy_code_zip() {
    echo ""
    echo ">>> Deploying function code (ZIP deploy)..."

    ZIP_FILE="/tmp/func-deploy-$(date +%s).zip"

    # Create ZIP with correct structure: function dirs at root + src/ + host.json + feeds.json + requirements.txt
    cd "$(dirname "$0")"
    zip -r "$ZIP_FILE" \
        RSSFetcher/ HealthEndpoint/ ArticleProcessor/ \
        src/ feeds.json host.json requirements.txt \
        -x '*/__pycache__/*' '*.pyc' '.venv/*' '.git/*' 2>/dev/null

    # Deploy ZIP to Function App
    az functionapp deployment source config-zip \
        --resource-group "$RG" \
        --name "$FUNC_APP" \
        --src "$ZIP_FILE"

    rm -f "$ZIP_FILE"
    echo "ZIP deployed."

    # Restart to pick up changes
    az functionapp restart --name "$FUNC_APP" --resource-group "$RG" 2>/dev/null || true

    verify_health
}

# ── Health verification ─────────────────────────────────────────────────────

verify_health() {
    echo ""
    echo "--- Verifying health endpoint ---"
    sleep 10  # allow cold start after deploy
    HEALTH_URL="https://${FUNC_APP}.azurewebsites.net/api/health"

    for i in 1 2 3 4 5; do
        STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$HEALTH_URL" 2>/dev/null || echo "000")
        echo "  Attempt $i: HTTP $STATUS"
        if [ "$STATUS" = "200" ]; then
            echo ""
            echo "Function App is running"
            curl -s "$HEALTH_URL" | python3 -m json.tool
            return 0
        fi
        sleep 5
    done
    echo "Health check returned non-200 after 5 attempts"
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
    --code-zip)
        deploy_code_zip
        ;;
    *)
        deploy_infra
        # Try func CLI first, fall back to ZIP deploy
        if command -v func &>/dev/null && func --version &>/dev/null 2>&1; then
            deploy_code || deploy_code_zip
        else
            deploy_code_zip
        fi
        ;;
esac

echo ""
echo "=== Deploy complete ==="
