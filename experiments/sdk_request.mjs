import Anthropic from '@anthropic-ai/sdk';

let input = '';
for await (const chunk of process.stdin) input += chunk;
const {baseURL, apiKey, streamed, ...params} = JSON.parse(input);
const client = new Anthropic({apiKey, baseURL, maxRetries: 0, timeout: 60000,
  defaultHeaders: {'X-WebCC-Tools': 'prompt-v1'}});
try {
  const message = streamed
    ? await client.messages.stream(params).finalMessage()
    : await client.messages.create(params);
  console.log(JSON.stringify({status: 200, message, sdk_streamed: streamed}));
} catch (error) {
  console.log(JSON.stringify({status: error.status ?? 0, error_type: error.name}));
  process.exitCode = 1;
}
