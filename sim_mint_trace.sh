#!/bin/bash
USER_KEY="$(grep USER_A_KEY /home/ubuntu/RLD/docker/.env | cut -d= -f2)"
DEPLOY=$(cat /home/ubuntu/RLD/docker/deployment.json)
MARKET_ID=$(echo "$DEPLOY" | jq -r .market_id)
BROKER_ADDR="0xfec5a0a8501bef27bd952368fcf313174d8cf661"
cast call --rpc-url http://127.0.0.1:8545 --trace --private-key $USER_KEY $BROKER_ADDR "modifyPosition(bytes32,int256,int256)" $MARKET_ID 0 50000000
