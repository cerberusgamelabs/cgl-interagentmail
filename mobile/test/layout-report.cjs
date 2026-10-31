// Test-only output: synchronous writes keep closed-pipe failures inside the
// runner's promise catch rather than Electron's uncaught stream-error dialog.
const {writeSync}=require('node:fs');
function reporter({write=writeSync,exit}) {
  return {
    record(value){write(1,JSON.stringify(value)+'\n');},
    fail(error){
      try{write(2,String(error?.stack || error)+'\n');}catch{/* Both pipes may be closed. */}
      exit(1); // Missing output is a failed run, never successful validation.
    }
  };
}
module.exports={reporter};
