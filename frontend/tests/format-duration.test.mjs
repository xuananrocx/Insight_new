import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'
const source = await readFile(new URL('../src/lib/format-duration.ts', import.meta.url), 'utf8')
const {outputText} = ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.ESNext}})
const {formatDuration} = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
test('duration units and missing values', () => {
  for (const [input, expected] of [[null,'-'],[0,'0 ms'],[999,'999 ms'],[1000,'1 s'],[1500,'1.5 s'],[60000,'1 min'],[90000,'1.5 min']]) assert.equal(formatDuration(input), expected)
})
