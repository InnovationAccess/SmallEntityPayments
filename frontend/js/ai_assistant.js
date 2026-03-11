/**
 * ai_assistant.js – Conversational AI Assistant panel logic (Tab 3).
 */

import { apiPost, setLoading, buildInteractiveTable, escHtml, enableAssignmentPopup } from './app.js';

const askBtn      = document.getElementById('ai-ask-btn');
const promptInput = document.getElementById('ai-prompt');
const chatDiv     = document.getElementById('ai-chat');
const sqlBlock    = document.getElementById('ai-sql-block');
const sqlContent  = document.getElementById('ai-sql-content');
const sqlToggle   = document.getElementById('ai-sql-toggle');
const resultsDiv  = document.getElementById('ai-results');

// Conversation history sent to the backend for context.
let chatHistory = [];

// SQL accordion toggle
sqlToggle.addEventListener('click', () => {
  const btn = sqlToggle.querySelector('.accordion-btn');
  const expanded = btn.getAttribute('aria-expanded') === 'true';
  btn.setAttribute('aria-expanded', String(!expanded));
  btn.textContent = expanded ? '+' : '\u2212';
  sqlContent.classList.toggle('hidden', expanded);
});

// Allow Ctrl/Cmd+Enter to submit
promptInput.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') askBtn.click();
});

askBtn.addEventListener('click', async () => {
  const prompt = promptInput.value.trim();
  if (!prompt) return;

  appendMessage('user', prompt);
  // Send history WITHOUT the current message (backend receives it as `prompt`)
  const historyToSend = [...chatHistory];
  chatHistory.push({ role: 'user', content: prompt });
  promptInput.value = '';

  setLoading(askBtn, true);
  resultsDiv.classList.add('hidden');

  // Thinking placeholder
  const thinkingEl = appendMessage('ai', '<span class="spinner"></span>Thinking\u2026', true);

  try {
    const res = await apiPost('/ai/ask', { prompt, history: historyToSend });

    // Replace thinking placeholder with actual answer
    thinkingEl.querySelector('.chat-bubble').innerHTML = formatAnswer(res.answer);

    // Save AI response to history
    chatHistory.push({ role: 'ai', content: res.answer });

    // Show generated SQL in accordion if present
    if (res.generated_sql) {
      sqlContent.textContent = res.generated_sql;
      sqlBlock.classList.remove('hidden');
      // Keep accordion collapsed
      const btn = sqlToggle.querySelector('.accordion-btn');
      btn.setAttribute('aria-expanded', 'false');
      btn.textContent = '+';
      sqlContent.classList.add('hidden');
    }

    // Show data rows if any
    if (res.rows && res.rows.length > 0) {
      resultsDiv.innerHTML = '';
      const hdr = document.createElement('div');
      hdr.className = 'results-header';
      hdr.innerHTML = `<strong>Data Results</strong><span class="results-count">${res.rows.length} record(s)</span>`;
      resultsDiv.appendChild(hdr);
      buildInteractiveTable(resultsDiv, res.rows);
      enableAssignmentPopup('#ai-results td[data-col="patent_number"]');
      resultsDiv.classList.remove('hidden');
    }
  } catch (err) {
    thinkingEl.querySelector('.chat-bubble').innerHTML =
      `<span style="color:var(--color-danger)">${escHtml(err.message)}</span>`;
    chatHistory.push({ role: 'ai', content: err.message });
  } finally {
    setLoading(askBtn, false);
    chatDiv.scrollTop = chatDiv.scrollHeight;
  }
});

/**
 * Append a chat message bubble and return the message element.
 */
function appendMessage(role, content, raw = false) {
  const msg = document.createElement('div');
  msg.className = `chat-msg ${role}`;

  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = role === 'user' ? '\uD83D\uDC64' : '\uD83E\uDD16';

  const bubble = document.createElement('div');
  bubble.className = 'chat-bubble';
  if (raw) {
    bubble.innerHTML = content;
  } else {
    bubble.textContent = content;
  }

  msg.appendChild(avatar);
  msg.appendChild(bubble);
  chatDiv.appendChild(msg);
  chatDiv.scrollTop = chatDiv.scrollHeight;
  return msg;
}

/**
 * Convert the AI answer text to safe HTML, preserving line breaks and
 * wrapping inline code (backtick-delimited) in <code> tags.
 */
function formatAnswer(text) {
  let html = escHtml(text ?? '');
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
  html = html.replace(/\n/g, '<br>');
  return html;
}
