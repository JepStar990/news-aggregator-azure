#!/usr/bin/env bash
# teardown.sh — Tear down the News Aggregator infrastructure.
#
# Usage:
#   ./teardown.sh              # Delete everything (prompts for confirmation)
#   ./teardown.sh --force       # Delete without confirmation
#   ./teardown.sh --keep-data   # Delete compute, keep Storage + data
#
# The Storage Account is protected from accidental deletion because:
#   1. State (ETag cache, feed health) is stored in Blob at system/feed-state.json
#   2. After teardown + redeploy, the Function App picks up where it left off
#   3. Use --keep-data to preserve this state across a full rebuild

set -euo pipefail
cd "$(dirname "$0")/.."

RG="rg-news-aggregator"
FUNC_APP="func-newsaggregator"
STORAGE="stnewsaggregator"  # will be discovered from deployment

echo "=== News Aggregator Azure — Teardown ==="

# ── Discover storage account name ────────────────────────────────────────────

STORAGE_NAME=$(az storage account list \
    --resource-group "$RG" \
    --query "[0].name" -o tsv 2>/dev/null || echo "")

if [ -z "$STORAGE_NAME" ]; then
    echo "No storage account found in $RG"
else
    echo "Found Storage Account: $STORAGE_NAME"
fi

# ── Confirmation ────────────────────────────────────────────────────────────

if [ "${1:-}" != "--force" ]; then
    echo ""
    echo "⚠️  This will delete resources in resource group: $RG"
    if [ "${1:-}" = "--keep-data" ]; then
        echo "   Mode: KEEP-DATA — deleting compute only, preserving Storage"
    else
        echo "   Mode: FULL — deleting all resources including Storage"
    fi
    echo ""
    read -rp "Type 'yes' to confirm: " confirm
    if [ "$confirm" != "yes" ]; then
        echo "Aborted."
        exit 0
    fi
fi

# ── Tear down ───────────────────────────────────────────────────────────────

if [ "${1:-}" = "--keep-data" ]; then
    echo ""
    echo ">>> Deleting Function App only (keeping Storage)..."
    az functionapp delete --name "$FUNC_APP" --resource-group "$RG" 2>/dev/null || true
    az appservice plan delete --name "asp-newsaggregator" --resource-group "$RG" --yes 2>/dev/null || true
    echo "✅ Compute deleted. Storage ($STORAGE_NAME) preserved."
else
    echo ""
    echo ">>> Deleting all resources..."
    # Delete the deployment first
    DEPLOYMENT_NAME=$(az deployment group list --resource-group "$RG" --query "[0].name" -o tsv 2>/dev/null || echo "")
    if [ -n "$DEPLOYMENT_NAME" ]; then
        az deployment group delete --name "$DEPLOYMENT_NAME" --resource-group "$RG" 2>/dev/null || true
    fi
    # Delete the entire resource group
    az group delete --name "$RG" --yes 2>/dev/null || true
    echo "✅ Resource group $RG deleted."
fi

echo ""
echo "=== Teardown complete ==="
echo "Run ./deploy.sh to spin up again."
