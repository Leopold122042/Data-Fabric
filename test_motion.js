import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
function harness(){
 const handlers={},mediaHandlers={},animations=[],attributes={},style={removeProperty(key){delete this[key];}};
 const summary={tagName:'SUMMARY',setAttribute(k,v){attributes[k]=v;},getBoundingClientRect(){return {height:28};},closest(s){return s==='summary'?this:s==='.mf-app'?{}:null;}};
 const details={tagName:'DETAILS',open:false,style,querySelector(){return summary;},getBoundingClientRect(){return {height:this.open?140:28};},animate(frames){const a={frames,cancelled:false,cancel(){this.cancelled=true;}};animations.push(a);return a;}};summary.parentElement=details;
 const media={matches:false,addEventListener(k,fn){mediaHandlers[k]=fn;}};
 vm.runInNewContext(readFileSync(new URL('./static/shared/motion.js',import.meta.url),'utf8'),{matchMedia:()=>media,document:{body:{},addEventListener(k,fn){(handlers[k]??=[]).push(fn);}},window:{addEventListener(){}},MutationObserver:class{observe(){}disconnect(){}},setTimeout});
 function click(){let prevented=false;handlers.click.forEach(fn=>fn({target:summary,preventDefault(){prevented=true;}}));return prevented;}
 return {details,attributes,media,mediaHandlers,animations,click,toggle(){handlers.toggle.forEach(fn=>fn({target:details}));}};
}
test('rapid reversal finishes closed and restores natural sizing',()=>{const h=harness();h.click();assert.equal(h.details.open,true);h.click();assert.equal(h.animations[0].cancelled,true);h.animations[1].onfinish();assert.equal(h.details.open,false);assert.equal(h.attributes['aria-expanded'],'false');assert.equal(h.details.style.overflow,undefined);});
test('enabling reduced motion settles an active disclosure immediately',()=>{const h=harness();h.click();h.media.matches=true;h.mediaHandlers.change();assert.equal(h.details.open,true);assert.equal(h.animations[0].cancelled,true);assert.equal(h.details.style.height,undefined);});
test('reduced motion retains native toggle behavior and correct accessibility state',()=>{const h=harness();h.media.matches=true;assert.equal(h.click(),false);assert.equal(h.animations.length,0);h.details.open=true;h.toggle();assert.equal(h.attributes['aria-expanded'],'true');h.details.open=false;h.toggle();assert.equal(h.attributes['aria-expanded'],'false');});
