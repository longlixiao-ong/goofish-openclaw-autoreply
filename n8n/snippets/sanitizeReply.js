function sanitizeReply(input, options = {}) {
  const maxReplyChars =
    Number.isFinite(Number(options.max_reply_chars)) && Number(options.max_reply_chars) > 0
      ? Number(options.max_reply_chars)
      : 80;

  let text = String(input ?? "");

  text = text.replace(/<think>[\s\S]*?<\/think>/gi, " ");
  text = text.replace(/```(?:thinking|reasoning|analysis)[\s\S]*?```/gi, " ");

  const markers = ["最终回复：", "最终回复:", "买家可见回复：", "买家可见回复:", "回复：", "回复:"];
  let cutPos = -1;
  let cutMarkerLength = 0;
  for (const marker of markers) {
    const pos = text.lastIndexOf(marker);
    if (pos > cutPos) {
      cutPos = pos;
      cutMarkerLength = marker.length;
    }
  }
  if (cutPos >= 0) {
    text = text.slice(cutPos + cutMarkerLength);
  }

  const blockedLinePrefix = /^(思考|分析|推理|判断|策略|步骤|理由|reasoning|analysis)/i;
  const markdownPrefix = /^\s*(?:[-*+]|#{1,6}|>|\d+[.)])\s*/;

  text = text
    .split(/\r?\n/)
    .map((line) => line.replace(markdownPrefix, "").trim())
    .filter((line) => line && !blockedLinePrefix.test(line))
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();

  if (!text) {
    return "";
  }

  if (/(微信|QQ|支付宝|银行卡|线下|转账|私聊|加我)/i.test(text)) {
    return "平台内沟通就行，合适可拍";
  }

  return text.slice(0, maxReplyChars);
}

module.exports = { sanitizeReply };
