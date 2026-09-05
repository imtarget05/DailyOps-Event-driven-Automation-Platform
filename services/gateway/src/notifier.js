'use strict';

const config = require('./config');

let transporter = null;
if (config.smtp.host) {
  const nodemailer = require('nodemailer');
  transporter = nodemailer.createTransport({
    host: config.smtp.host,
    port: config.smtp.port,
    secure: config.smtp.port === 465,
    auth: config.smtp.user ? { user: config.smtp.user, pass: config.smtp.pass } : undefined,
  });
}

/**
 * Sends a notification. Secrets live ONLY in gateway env (never in events).
 * Falls back to structured log when no channel is configured, so the demo
 * stack works out of the box.
 */
async function send({ channel, text, subject }) {
  if (channel === 'telegram' && config.telegramBotToken && config.telegramChatId) {
    const res = await fetch(`https://api.telegram.org/bot${config.telegramBotToken}/sendMessage`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ chat_id: config.telegramChatId, text }),
    });
    if (!res.ok) throw new Error(`telegram API returned ${res.status}`);
    return { sent: true, channel: 'telegram' };
  }
  if (channel === 'email' && transporter) {
    await transporter.sendMail({ from: config.smtp.from, to: config.smtp.to, subject: subject || 'DailyOps notification', text });
    return { sent: true, channel: 'email' };
  }
  console.log(`[notifier:${channel || 'log'}] ${text}`);
  return { sent: false, channel: 'log' };
}

module.exports = { send };
