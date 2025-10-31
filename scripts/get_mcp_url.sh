#!/bin/bash
# Script to calculate and display the full MCP endpoint URL

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get API key from environment or argument
API_KEY="${MCP_API_KEY:-$1}"

if [ -z "$API_KEY" ]; then
    echo -e "${YELLOW}Usage:${NC}"
    echo "  $0 <API_KEY>"
    echo "  or set MCP_API_KEY environment variable"
    echo ""
    echo "Examples:"
    echo "  export MCP_API_KEY='your-api-key'"
    echo "  $0"
    echo ""
    echo "  $0 'your-api-key'"
    exit 1
fi

# Get domain from argument or default
DOMAIN="${2:-your-domain.com}"

# Calculate MD5 hash
API_KEY_HASH=$(echo -n "$API_KEY" | md5sum | awk '{print $1}')

# Display results
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  MCP Endpoint URL Calculator${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${BLUE}API Key:${NC}         $API_KEY"
echo -e "${BLUE}API Key Hash:${NC}    $API_KEY_HASH"
echo -e "${BLUE}Domain:${NC}          $DOMAIN"
echo ""
echo -e "${GREEN}Endpoints:${NC}"
echo -e "  ${BLUE}MCP (authenticated):${NC}  https://$DOMAIN/mcp/$API_KEY/$API_KEY_HASH"
echo -e "  ${BLUE}Health (public):${NC}      https://$DOMAIN/health"
echo ""
echo -e "${YELLOW}Note:${NC} Keep the MCP URL confidential. It contains authentication credentials."
echo ""

# Option to copy to clipboard (if available)
if command -v pbcopy &> /dev/null; then
    echo "https://$DOMAIN/mcp/$API_KEY/$API_KEY_HASH" | pbcopy
    echo -e "${GREEN}✓${NC} MCP endpoint URL copied to clipboard (macOS)"
elif command -v xclip &> /dev/null; then
    echo "https://$DOMAIN/mcp/$API_KEY/$API_KEY_HASH" | xclip -selection clipboard
    echo -e "${GREEN}✓${NC} MCP endpoint URL copied to clipboard (Linux)"
elif command -v clip.exe &> /dev/null; then
    echo "https://$DOMAIN/mcp/$API_KEY/$API_KEY_HASH" | clip.exe
    echo -e "${GREEN}✓${NC} MCP endpoint URL copied to clipboard (Windows)"
fi

echo ""
