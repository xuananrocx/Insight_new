import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

async function loadPlugin(name) {
  const source = await readFile(new URL(`../src/lib/${name}.ts`, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}
const { remarkCjkStrong } = await loadPlugin('remark-cjk-strong')
const { remarkCitations } = await loadPlugin('remark-citations')
const render = (source, plugins = []) => renderToStaticMarkup(React.createElement(Markdown, { remarkPlugins: [remarkGfm, remarkCjkStrong, ...plugins] }, source))

test('renders the reported answer and multiple punctuation boundaries', () => {
  const body = '更新更接近行情事件驱动；你看到的回调频率还受递交间隔影响。'
  assert.equal(render(`简答是：**${body}**可先检`), `<p>简答是：<strong>${body}</strong>可先检</p>`)
  assert.equal(render('**第一项。**后续，**第二项！**继续'), '<p><strong>第一项。</strong>后续，<strong>第二项！</strong>继续</p>')
})

test('keeps code, escaped stars, entities and incomplete streaming text literal', () => {
  for (const source of ['`**内容。**后续`', '```text\n**内容。**后续\n```', '\\*\\*内容。\\*\\*后续', '&#42;&#42;内容。&#42;&#42;后续', '**内容。*', '**内容。', '** 空格。**后续']) {
    assert.equal(render(source), renderToStaticMarkup(React.createElement(Markdown, { remarkPlugins: [remarkGfm] }, source)), source)
  }
})

test('preserves standard formatting and citation links inside repaired bold', () => {
  assert.equal(render('**普通加粗**，*斜体*'), '<p><strong>普通加粗</strong>，<em>斜体</em></p>')
  assert.match(render('**内容【cite:1】。**后续', [[remarkCitations, { count: 1, prefix: 'ref' }]]), /<strong>内容<a href="#ref-1">\[1\]<\/a>。<\/strong>后续/)
  assert.equal(render('**内容【cite:1】。**后续', [[remarkCitations, { hidden: true }]]), '<p><strong>内容。</strong>后续</p>')
})


test('only explicit citations are linked or hidden; array indices survive', () => {
  const source = '数组下标与档位对应：\n\n- `[0]`：买一／卖一\n- `[1]`：买二／卖二\n- `[9]`：买十／卖十\n\narray[1] [0] [9]。事实【cite:1】无效【cite:99】\n\n```python\na[0] = a[9]\n```\n\n[文档](https://example.com)';
  for (const hidden of [true, false]) {
    const html = render(source, [[remarkCitations, { count: 1, prefix: 'ref', hidden }]])
    for (const i of [0, 1, 9]) assert.ok(html.includes(`<code>[${i}]</code>`))
    assert.ok(html.includes('array[1] [0] [9]'))
    assert.ok(html.includes('<ul>'))
    assert.ok(html.includes('a[0] = a[9]'))
    assert.ok(html.includes('href="https://example.com"'))
    assert.equal(html.includes('href="#ref-1"'), !hidden)
    assert.ok(!html.includes('cite:'))
  }
})
