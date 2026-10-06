import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { FileBlob, PresentationFile } from '@oai/artifact-tool';
const build=path.dirname(fileURLToPath(import.meta.url));
const source=path.join(path.dirname(build),'outputs','钱途有界_当前功能答辩_10页_交付版.pptx');
const actual=await PresentationFile.importPptx(await FileBlob.load(source));
const dest=path.join(build,'deck-final-file-previews');
await fs.mkdir(dest,{recursive:false});
for(const [index,slide] of [...actual.slides.items].entries()){
 const preview=await actual.export({slide,format:'png',scale:1});
 await fs.writeFile(path.join(dest,`slide-${String(index+1).padStart(2,'0')}.png`),new Uint8Array(await preview.arrayBuffer()));
}
console.log(JSON.stringify({actual_final_pptx:source,rendered_slides:actual.slides.items.length,renderer:'Artifact Tool import/export',native_powerpoint_open:'NOT_RUN'}));
