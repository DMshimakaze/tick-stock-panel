const VIEW = 'market_volpulse_view_v1';
const COMMAND = 'market_volpulse_command_v1';
const RECEIPT = 'market_volpulse_receipt_v1';
const SCOPE = 'market.volpulse';

export function bridge(api) {
  async function read(table) {
    const response = await api.extDataRows(table, { limit: 1 });
    const row = response.rows?.[0];
    return row?.scope === SCOPE ? JSON.parse(row.payload_json) : null;
  }
  return {
    view: () => read(VIEW),
    receipt: () => read(RECEIPT),
    send: command => api.extDataIngest(COMMAND, {
      rows: [{ scope: SCOPE, payload_json: JSON.stringify(command) }],
    }),
  };
}

export function requestId() {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2, '0')).join('');
}
