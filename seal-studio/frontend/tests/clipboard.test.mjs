import test from 'node:test';
import assert from 'node:assert/strict';
import { pastedImages } from '../src/lib/clipboard.ts';
const photo = new File(['fixture'],'photo.png',{type:'image/png'});
test('clipboard items win over mirrored files without duplicates', () => {
  assert.deepEqual(pastedImages({items:[{kind:'file',type:'image/png',getAsFile:()=>photo}],files:[photo]}),[photo]);
});
test('files fallback supports browsers without clipboard items', () => {
  assert.deepEqual(pastedImages({items:[],files:[photo]}),[photo]);
});
test('text, non-images and null items are not attachments', () => {
  assert.deepEqual(pastedImages({items:[{kind:'string',type:'text/plain'},{kind:'file',type:'image/png',getAsFile:()=>null}],files:[new File(['text'],'a.txt',{type:'text/plain'})]}),[]);
});
test('items path returns result when files collection is empty', () => {
  assert.deepEqual(pastedImages({items:[{kind:'file',type:'image/png',getAsFile:()=>photo}],files:[]}),[photo]);
});
test('string-kind item with image mime is not returned even when type matches', () => {
  assert.deepEqual(pastedImages({items:[{kind:'string',type:'image/png',getAsFile:()=>photo}],files:[]}),[]);
});
test('file-kind item with non-image mime is not returned even when kind matches', () => {
  const vid = new File(['fixture'],'clip.mp4',{type:'video/mp4'});
  assert.deepEqual(pastedImages({items:[{kind:'file',type:'video/mp4',getAsFile:()=>vid}],files:[]}),[]);
});
