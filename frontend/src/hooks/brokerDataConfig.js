export const BROKER_DATA_QUERY = `
  query BrokerData($owner: String!, $marketId: String!, $brokerAddress: String) {
    brokers(marketId: $marketId, owner: $owner) {
      address
      marketId
      owner
      createdBlock
      activeTokenId
      wausdcBalance
      wrlpBalance
      debtPrincipal
      updatedBlock
      isFrozen
      isLiquidated
    }
    brokerProfile(owner: $owner, marketId: $marketId, brokerAddress: $brokerAddress)
    poolSnapshot(marketId: $marketId) {
      markPrice indexPrice tick
      normalizationFactor
    }
    brokerOperations(owner: $owner, marketId: $marketId, brokerAddress: $brokerAddress)
  }
`;

export function resolveSelectedBrokerAddress(brokerAccounts = [], selectedBrokerAddress = null) {
  if (!brokerAccounts.length) return null;

  const selected = selectedBrokerAddress?.toLowerCase();
  const selectedStillAvailable = brokerAccounts.some(
    (broker) => broker.address?.toLowerCase() === selected,
  );

  return selectedStillAvailable ? selectedBrokerAddress : brokerAccounts[0].address;
}

export function toHumanTokenAmount(raw, decimals = 6) {
  if (raw == null || raw === "") return 0;
  const value = Number(raw);
  if (!Number.isFinite(value)) return 0;
  return value / 10 ** decimals;
}

export function getBrokerUpdatedBlock(broker) {
  const value = Number(broker?.updatedBlock ?? broker?.updated_block ?? broker?.createdBlock ?? broker?.created_block ?? 0);
  return Number.isFinite(value) ? value : 0;
}

export function getActiveBrokerState(brokerAccounts = [], selectedBrokerAddress = null, profile = null) {
  const activeBrokerAddress = resolveSelectedBrokerAddress(brokerAccounts, selectedBrokerAddress);
  const activeBroker = activeBrokerAddress
    ? brokerAccounts.find((broker) => broker.address?.toLowerCase() === activeBrokerAddress.toLowerCase()) || null
    : null;
  const balanceSource = activeBroker || profile || null;

  return {
    activeBrokerAddress,
    activeBroker,
    hasTradingBroker: !!activeBrokerAddress,
    brokerBalance: toHumanTokenAmount(balanceSource?.wausdcBalance ?? balanceSource?.wausdc_balance),
    wrlpBalance: toHumanTokenAmount(balanceSource?.wrlpBalance ?? balanceSource?.wrlp_balance),
    debtPrincipal: toHumanTokenAmount(balanceSource?.debtPrincipal ?? balanceSource?.debt_principal),
    updatedBlock: getBrokerUpdatedBlock(activeBroker || profile),
  };
}

export function isBrokerSyncedToBlock(broker, targetBlock) {
  if (!targetBlock) return true;
  return getBrokerUpdatedBlock(broker) >= Number(targetBlock);
}


export const LEGACY_BROKER_DATA_QUERY = `
  query BrokerData($owner: String!, $marketId: String!) {
    brokers(marketId: $marketId) {
      address
      marketId
      owner
      createdBlock
      activeTokenId
      wausdcBalance
      wrlpBalance
      debtPrincipal
      isFrozen
      isLiquidated
    }
    brokerProfile(owner: $owner)
    poolSnapshot(marketId: $marketId) {
      markPrice indexPrice tick
      normalizationFactor
    }
    brokerOperations(owner: $owner)
  }
`;

export function isScopedBrokerQueryUnsupported(error) {
  const messages = error?.errors?.map((entry) => entry?.message || "") || [error?.message || ""];
  return messages.some((message) =>
    (message.includes("Unknown argument") &&
      (message.includes("owner") || message.includes("marketId") || message.includes("brokerAddress"))) ||
    (message.includes("Cannot query field") && message.includes("updatedBlock")),
  );
}
