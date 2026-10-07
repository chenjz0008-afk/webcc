import MarkdownIt from 'markdown-it';
let input = '';
for await (const chunk of process.stdin) input += chunk;
const {original, current, expected} = JSON.parse(input);
const markdown = new MarkdownIt({html: false});
const signature = tokens => tokens.map(token => ({type: token.type, tag: token.tag,
  nesting: token.nesting, attrs: token.attrs, map: token.map,
  children: token.children ? signature(token.children) : null}));
const result = {pass: true, documents: {}};
for (const id of ['A', 'B']) {
  const before = markdown.parse(original[id], {}), after = markdown.parse(current[id], {});
  const sameStructure = JSON.stringify(signature(before)) === JSON.stringify(signature(after));
  const expectedHtml = markdown.render(expected[id]) === markdown.render(current[id]);
  result.documents[id] = {same_structure: sameStructure, expected_html: expectedHtml,
    tables: after.filter(t => t.type === 'table_open').length,
    lists: after.filter(t => t.type === 'bullet_list_open').length,
    headings: after.filter(t => t.type === 'heading_open').length};
  result.pass &&= sameStructure && expectedHtml;
}
console.log(JSON.stringify(result));
