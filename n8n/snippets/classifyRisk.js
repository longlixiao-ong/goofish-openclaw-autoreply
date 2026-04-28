const LOW_KEYWORDS = ["还在吗", "在吗", "包邮吗", "今天能发吗", "能便宜吗", "最低多少", "小刀"];
const HIGH_KEYWORDS = [
  "退款",
  "投诉",
  "售后",
  "举报",
  "假货",
  "骗子",
  "微信",
  "QQ",
  "支付宝",
  "银行卡",
  "线下",
  "转账",
  "私聊",
  "加我",
];

function classifyRisk(message) {
  const text = String(message ?? "").trim();
  if (!text) {
    return { risk: "medium", reason: "empty_message" };
  }

  const highHit = HIGH_KEYWORDS.find((keyword) => text.includes(keyword));
  if (highHit) {
    return { risk: "high", reason: `hit_high_keyword:${highHit}` };
  }

  const lowHit = LOW_KEYWORDS.find((keyword) => text.includes(keyword));
  if (lowHit) {
    return { risk: "low", reason: `hit_low_keyword:${lowHit}` };
  }

  return { risk: "medium", reason: "default_medium" };
}

module.exports = { classifyRisk };
