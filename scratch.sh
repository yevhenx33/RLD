#!/bin/bash
source /home/ubuntu/RLD/docker/.env
RPC="http://127.0.0.1:8545"

# Use USER_A
export ETH_RPC_URL=$RPC
export ETH_FROM=$(cast wallet address --private-key $USER_A_KEY)

echo "User: $ETH_FROM"

DEPLOY=$(cat /home/ubuntu/RLD/docker/deployment.json)
BROKER_ROUTER=$(echo "$DEPLOY" | jq -r .broker_router)
BROKER_FACTORY=$(echo "$DEPLOY" | jq -r .broker_factory)
WAUSDC=$(echo "$DEPLOY" | jq -r .wausdc)
POS_TOKEN=$(echo "$DEPLOY" | jq -r .position_token)
FEE=$(echo "$DEPLOY" | jq -r .pool_fee)
TICK_SPACING=$(echo "$DEPLOY" | jq -r .tick_spacing)
HOOK=$(echo "$DEPLOY" | jq -r .twamm_hook)
if [ "$HOOK" == "null" ]; then HOOK="0x0000000000000000000000000000000000000000"; fi


AMOUNT_IN_WEI=50000000 # 50 USD

echo "BROKER_ROUTER: $BROKER_ROUTER"

# Get Broker
BROKER_ADDR=$(cast call $BROKER_FACTORY "getBroker(address,address)(address)" $WAUSDC $POS_TOKEN)
echo "BROKER_ADDR: $BROKER_ADDR"

if [ "$BROKER_ADDR" == "0x0000000000000000000000000000000000000000" ]; then
   BROKER_ADDR=$(cast send --private-key $USER_A_KEY $BROKER_FACTORY "deployBroker(address,address)(address)" $WAUSDC $POS_TOKEN | grep contractAddress | awk '{print $2}')
   echo "Deployed Broker: $BROKER_ADDR"
fi

# Set operator
cast send --private-key $USER_A_KEY $BROKER_ADDR "setOperator(address,bool)" $BROKER_ROUTER true

# Approve Router
cast send --private-key $USER_A_KEY $WAUSDC "approve(address,uint256)" $BROKER_ROUTER $AMOUNT_IN_WEI

# Sort pool key
if [[ "${WAUSDC,,}" < "${POS_TOKEN,,}" ]]; then
  T0=$WAUSDC
  T1=$POS_TOKEN
else
  T0=$POS_TOKEN
  T1=$WAUSDC
fi

# executeLong (address broker, uint256 amountIn, (address,address,uint24,int24,address) poolKey)
cast call --private-key $USER_A_KEY $BROKER_ROUTER "executeLong(address,uint256,(address,address,uint24,int24,address))(uint256)" \
    $BROKER_ADDR \
    $AMOUNT_IN_WEI \
    "($T0,$T1,$FEE,$TICK_SPACING,$HOOK)"
