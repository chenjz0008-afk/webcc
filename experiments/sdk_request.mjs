import Anthropic from '@anthropic-ai/sdk';

let input = '';
for await (const chunk of process.stdin) input += chunk;
const {baseURL, apiKey, streamed, ...params} = JSON.parse(input);
const client = new Anthropic({apiKey, baseURL, maxRetries: 0, timeout: 60000,
  defaultHeaders: {'X-WebCC-Tools': 'prompt-v1'}});
try {
  let message, requestId;
  if (streamed) {
    const stream = client.messages.stream(params);
    message = await stream.finalMessage();
    requestId = stream.request_id;
  } else {
    const result = await client.messages.create(params).withResponse();
    message = result.data;
    requestId = result.request_id;
  }
  console.log(JSON.stringify({status: 200, message, sdk_streamed: streamed, sdk_request_id: requestId}));
} catch (error) {
  console.log(JSON.stringify({status: error.status ?? 0, error_type: error.name,
    sdk_request_id: error.requestID ?? null, error_kind: error.type ?? null}));
  process.exitCode = 1;
}
