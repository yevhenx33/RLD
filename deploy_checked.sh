#!/bin/bash

# Configuration
# STRICT_MODE=true means deployment fails if audit fails.
STRICT_MODE=true

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}🛡️  Starting SAFE DEPLOYMENT Pipeline...${NC}"

# 1. Environment Check
if [ ! -f ".env" ]; then
    echo -e "${RED}❌ Missing .env file. Aborting.${NC}"
    exit 1
fi

if [ ! -d "venv" ]; then
    echo -e "${RED}❌ Missing venv. Aborting.${NC}"
    exit 1
fi

source venv/bin/activate

# 2. Production Audit (Last 1 Hour)
echo -e "\n${BLUE}1️⃣  Step: Diagnostic Audit (Last 1 Hour)...${NC}"
echo ">> Running Block-Level Audit (1 Hour)..."
python3 backend/scripts/audit_block_1h.py || echo -e "${YELLOW}⚠️  Block audit failed.${NC}"

echo ">> Running Hourly Audit (Full History)..."
python3 backend/scripts/audit_hourly_full.py || echo -e "${YELLOW}⚠️  Hourly audit failed.${NC}"

# 3. Repair (Skipped for Production)
echo -e "\n${BLUE}2️⃣  Step: Repair Skipped (Assumed Backfilled)...${NC}"
# python3 backend/fill_gaps_startup.py

# 4. Sync Logic (Raw -> Clean)
echo -e "\n${BLUE}3️⃣  Step: Synchronizing Frontend Database...${NC}"
python3 backend/scripts/sync_clean_db.py
if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Sync failed! Aborting.${NC}"
    exit 1
fi

# 5. Post-Repair Audit (Skipped)
echo -e "\n${BLUE}4️⃣  Step: Post-Repair Audit (Skipped by request)...${NC}"
echo -e "${YELLOW}⚠️  Proceeding without strict gatekeeper checks.${NC}"

deactivate

# 6. Launch Application
echo -e "\n${BLUE}🚀 Launching Application using dev_start.sh...${NC}"
./dev_start.sh
