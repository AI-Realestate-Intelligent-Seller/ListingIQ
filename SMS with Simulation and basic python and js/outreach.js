function cleanSentence(value = '') {
  return String(value).trim().replace(/[\s.?!]+$/, '');
}

function buildSingleLeadContext(propertyAddress, outreachReason) {
  return {
    available: true,
    property_address: String(propertyAddress).trim(),
    lead_source: 'Manual single conversation',
    outreach_reason: String(outreachReason).trim(),
    phone_number_source: {
      available: false,
      safe_response: 'The manually entered lead does not state how the phone number was sourced.'
    },
    property_details: {}
  };
}

function buildInitialOutreach(name, propertyAddress, outreachReason) {
  const firstName = String(name).trim().split(/\s+/)[0];
  const address = cleanSentence(propertyAddress);
  const reason = cleanSentence(outreachReason);
  return `Hey ${firstName}, I’m reaching out about ${address}. ${reason}. Would you be open to a brief conversation? Bobbie Fisher – RE/MAX`;
}

export { buildInitialOutreach, buildSingleLeadContext };
