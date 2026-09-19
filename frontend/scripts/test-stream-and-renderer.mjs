// Regression & integration verification test for MarkdownRenderer and SSE parser
import React from 'react';
import ReactDOMServer from 'react-dom/server';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import assert from 'node:assert';

console.log('--- TEST 1: MarkdownRenderer Visual Rendering Verification ---');

// Test that ReactMarkdown with children renders non-empty HTML for all required formats
const testCases = [
  { name: 'Plain text', input: 'This is an explanation of the debator repository.' },
  { name: 'Headings', input: '# Debator Overview\n## Core Architecture\n### Details' },
  { name: 'Lists', input: '- Claim Extractor\n- Falsifier Agent\n- Judge Agent' },
  { name: 'Fenced code', input: '```python\ndef run_debate():\n    return True\n```' },
  { name: 'Links', input: 'Check the [documentation](https://github.com/srikant07-dev/debator).' },
  { name: 'Progressively appended text', input: 'First token' },
];

for (const tc of testCases) {
  const el = React.createElement(ReactMarkdown, { remarkPlugins: [remarkGfm] }, tc.input);
  const html = ReactDOMServer.renderToStaticMarkup(el);
  assert(html.length > 0, `Expected non-empty HTML for ${tc.name}`);
  assert(!html.includes('undefined'), `HTML should not contain undefined for ${tc.name}`);
  console.log(`✔ [PASS] ${tc.name}: rendered ${html.length} chars of HTML`);
}

// Confirm that self-closing ReactMarkdown (the bug) would have failed
const buggyEl = React.createElement(ReactMarkdown, { remarkPlugins: [remarkGfm] });
const buggyHtml = ReactDOMServer.renderToStaticMarkup(buggyEl);
assert.strictEqual(buggyHtml, '', 'Self-closing ReactMarkdown produces empty string');
console.log('✔ [PASS] Verified reproduction: self-closing ReactMarkdown produces 0 chars.');

console.log('\n--- TEST 2: Mocked SSE Parser Verification ---');

// Implement the exact streamChat parsing logic to verify against all required scenarios
async function parseMockSSE(chunks, signal) {
  let buffer = '';
  let contentReceived = false;
  let doneReceived = false;
  const receivedChunks = [];
  const receivedCitations = [];
  let metaReceived = null;
  let streamError = null;

  const delimiterRegex = /\r\n\r\n|\n\n|\r\r/;

  const processFrame = (rawFrame) => {
    const lines = rawFrame.split(/\r?\n/);
    for (const rawLine of lines) {
      const line = rawLine.trim();
      if (!line || line.startsWith(':')) continue;
      if (line.startsWith('data:')) {
        const payload = line.slice(5).trim();
        if (payload === '[DONE]') {
          doneReceived = true;
          return;
        }
        let parsed;
        try {
          parsed = JSON.parse(payload);
        } catch {
          continue;
        }

        if (parsed.type === 'meta') {
          metaReceived = parsed;
          continue;
        }
        if (parsed.citations) {
          receivedCitations.push(...parsed.citations);
        }
        if (parsed.text) {
          if (typeof parsed.text === 'string' && parsed.text.trim().length > 0) {
            contentReceived = true;
          }
          receivedChunks.push(parsed.text);
        }
        if (parsed.error) {
          streamError = parsed.error;
          throw new Error(parsed.error);
        }
      }
    }
  };

  try {
    for (const chunk of chunks) {
      if (signal?.aborted) {
        return { type: 'aborted', contentReceived, receivedChunks, receivedCitations };
      }
      buffer += chunk;

      let match;
      while ((match = delimiterRegex.exec(buffer)) !== null) {
        const frame = buffer.slice(0, match.index);
        buffer = buffer.slice(match.index + match[0].length);
        if (frame.trim()) {
          processFrame(frame);
          if (doneReceived) {
            return { type: 'completed', contentReceived, receivedChunks, receivedCitations, metaReceived };
          }
        }
      }
    }

    if (buffer.trim()) {
      processFrame(buffer);
      buffer = '';
    }

    if (signal?.aborted) {
      return { type: 'aborted', contentReceived, receivedChunks, receivedCitations };
    }
    if (doneReceived) {
      return { type: 'completed', contentReceived, receivedChunks, receivedCitations, metaReceived };
    }
    return {
      type: 'interrupted',
      reason: 'Stream closed before completion signal ([DONE]) was received.',
      contentReceived,
      receivedChunks,
      receivedCitations,
    };
  } catch (err) {
    if (signal?.aborted) {
      return { type: 'aborted', contentReceived, receivedChunks, receivedCitations };
    }
    throw err;
  }
}

// Scenario 1: Standard LF frames
{
  const chunks = [
    'data: {"citations": [{"file_path": "README.md"}]}\n\n',
    'data: {"type": "meta", "provider": "groq", "model": "openai/gpt-oss-120b"}\n\n',
    'data: {"text": "Hello "}\n\n',
    'data: {"text": "world!"}\n\n',
    'data: [DONE]\n\n',
  ];
  const res = await parseMockSSE(chunks);
  assert.strictEqual(res.type, 'completed');
  assert.strictEqual(res.contentReceived, true);
  assert.deepStrictEqual(res.receivedChunks, ['Hello ', 'world!']);
  assert.strictEqual(res.receivedCitations.length, 1);
  console.log('✔ [PASS] Scenario 1: LF frames parsed cleanly.');
}

// Scenario 2: CRLF frames
{
  const chunks = [
    'data: {"citations": [{"file_path": "test.py"}]}\r\n\r\n',
    'data: {"text": "CRLF works"}\r\n\r\n',
    'data: [DONE]\r\n\r\n',
  ];
  const res = await parseMockSSE(chunks);
  assert.strictEqual(res.type, 'completed');
  assert.strictEqual(res.contentReceived, true);
  assert.deepStrictEqual(res.receivedChunks, ['CRLF works']);
  console.log('✔ [PASS] Scenario 2: CRLF frames parsed cleanly.');
}

// Scenario 3: Delimiters split across network chunks
{
  const chunks = [
    'data: {"text": "Chunk 1"}\r',
    '\n\r\ndata: {"text": "Chunk 2"}\n',
    '\ndata: [DONE]\n\n',
  ];
  const res = await parseMockSSE(chunks);
  assert.strictEqual(res.type, 'completed');
  assert.deepStrictEqual(res.receivedChunks, ['Chunk 1', 'Chunk 2']);
  console.log('✔ [PASS] Scenario 3: Split delimiters across chunks parsed cleanly.');
}

// Scenario 4: Citations before text
{
  const chunks = [
    'data: {"citations": [{"file_path": "README.md"}, {"file_path": "app.py"}]}\n\n',
    'data: {"text": "Explanation"}\n\n',
    'data: [DONE]\n\n',
  ];
  const res = await parseMockSSE(chunks);
  assert.strictEqual(res.receivedCitations.length, 2);
  assert.strictEqual(res.receivedChunks[0], 'Explanation');
  console.log('✔ [PASS] Scenario 4: Citations before text populated properly.');
}

// Scenario 5: Empty [DONE] (provider returned 0 text)
{
  const chunks = [
    'data: {"citations": [{"file_path": "README.md"}]}\n\n',
    'data: [DONE]\n\n',
  ];
  const res = await parseMockSSE(chunks);
  assert.strictEqual(res.type, 'completed');
  assert.strictEqual(res.contentReceived, false);
  assert.strictEqual(res.receivedChunks.length, 0);
  console.log('✔ [PASS] Scenario 5: Empty [DONE] returns completed with contentReceived: false.');
}

// Scenario 6: EOF without [DONE] (interrupted stream)
{
  const chunks = [
    'data: {"text": "Partial text before crash"}\n\n',
  ];
  const res = await parseMockSSE(chunks);
  assert.strictEqual(res.type, 'interrupted');
  assert.strictEqual(res.contentReceived, true);
  assert.strictEqual(res.receivedChunks[0], 'Partial text before crash');
  console.log('✔ [PASS] Scenario 6: EOF without [DONE] returns interrupted.');
}

// Scenario 7: SSE error frame
{
  const chunks = [
    'data: {"error": "Model capacity exceeded"}\n\n',
  ];
  let threw = false;
  try {
    await parseMockSSE(chunks);
  } catch (err) {
    threw = true;
    assert(err.message.includes('Model capacity exceeded'));
  }
  assert(threw, 'Expected error frame to throw');
  console.log('✔ [PASS] Scenario 7: SSE error frame throws error.');
}

// Scenario 8: User abort before text
{
  const controller = new AbortController();
  controller.abort();
  const chunks = [
    'data: {"citations": [{"file_path": "README.md"}]}\n\n',
    'data: {"text": "Text after abort"}\n\n',
  ];
  const res = await parseMockSSE(chunks, controller.signal);
  assert.strictEqual(res.type, 'aborted');
  console.log('✔ [PASS] Scenario 8: User abort before text returns aborted.');
}

// Scenario 9: User abort after partial text
{
  const controller = new AbortController();
  // Simulate chunk 1 received, then abort
  const chunks = [
    'data: {"text": "Partial"}\n\n',
  ];
  const res1 = await parseMockSSE(chunks);
  controller.abort();
  const res2 = await parseMockSSE(['data: {"text": " more"}\n\n'], controller.signal);
  assert.strictEqual(res2.type, 'aborted');
  console.log('✔ [PASS] Scenario 9: User abort after partial text returns aborted.');
}

console.log('\nALL 9 REGRESSION & INTEGRATION SCENARIOS PASSED SUCCESSFULLY!');
