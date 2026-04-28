const RULES = [
  { pattern: /还在吗|在吗/, reply: "在的，喜欢可拍" },
  { pattern: /包邮吗/, reply: "默认不包，合适可以小刀" },
  { pattern: /今天能发吗/, reply: "今天拍下可以尽快发" },
  { pattern: /能便宜吗|小刀/, reply: "可以小刀，你出个合适价" },
  { pattern: /最低多少/, reply: "价格比较实，合适可拍" },
];

function getLowRiskReply(message) {
  const text = String(message ?? "").trim();
  if (!text) {
    return "";
  }

  const matched = RULES.find((rule) => rule.pattern.test(text));
  return matched ? matched.reply : "";
}

module.exports = { getLowRiskReply };
