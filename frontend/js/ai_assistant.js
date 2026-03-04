/**
 * ai_assistant.js – Gemini AI Assistant panel logic.
 */

import { apiPost, setLoading, buildPatentTable, escHtml } from './app.js';

const askBtn      = document.getElementById('ai-ask-btn');
const promptInput = document.getElementById('ai-prompt');
const chatDiv     = document.getElementById('ai-chat');
const sqlBlock    = document.getElementById('ai-sql-block');
const sqlContent  = document.getElementById('ai-sql-content');
const resultsDiv  = document.getElementById('ai-results');

// Allow Ctrl/Cmd+Enter to submit
promptInput.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') askBtn.click();
});

askBtn.addEventListener('click', async () => {
  const prompt = promptInput.value.trim();
  if (!prompt) return;

  appendMessage('user', prompt);
  promptInput.value = '';

  setLoading(askBtn, true);
  sqlBlock.classList.add('hidden');
  resultsDiv.classList.add('hidden');

  // Thinking placeholder
  const thinkingEl = appendMessage('ai', '<span class="spinner"></span>Thinking…', true);

  try {
    const res = await apiPost('/ai/ask', { prompt });

    // Replace thinking placeholder with actual answer
    thinkingEl.querySelector('.chat-bubble').innerHTML = formatAnswer(res.answer);

    // Show generated SQL if present
    if (res.generated_sql) {
      sqlContent.textContent = res.generated_sql;
      sqlBlock.classList.remove('hidden');
    }

    // Show data rows if any
    if (res.rows && res.rows.length > 0) {
      resultsDiv.innerHTML = `
        <div class="results-header">
          <strong>Data Results</strong>
          <span class="results-count">${res.rows.length} record(s)</span>
        </div>
        ${buildPatentTable(res.rows)}`;
      resultsDiv.classList.remove('hidden');
    }
  } catch (err) {
    thinkingEl.querySelector('.chat-bubble').innerHTML =
      `<span style="color:var(--color-danger)">${escHtml(err.message)}</span>`;
  } finally {
    setLoading(askBtn, false);
    chatDiv.scrollTop = chatDiv.scrollHeight;
  }
});

/**
 * Append a chat message bubble and return the message element.
 * If raw is true, innerHTML is used directly (for spinners etc.).
 */
function appendMessage(role, content, raw = false) {
  const msg = document.createElement('div');
  msg.className = `chat-msg ${role}`;

  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = role === 'user' ? '👤' : '🤖';

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
  // Escape HTML first
  let html = escHtml(text ?? '');
  // Inline code
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
  // Line breaks
  html = html.replace(/\n/g, '<br>');
  return html;
}
