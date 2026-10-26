// Pyodide 上で pdf2mscz (Python) を動かすワーカー
importScripts("https://cdn.jsdelivr.net/pyodide/v0.28.3/full/pyodide.js");

let py = null;

async function init() {
  postMessage({ type: "status", text: "Python 実行環境を読み込み中…" });
  py = await loadPyodide();
  await py.loadPackage("micropip");
  postMessage({ type: "status", text: "PDF 解析ライブラリ (PyMuPDF) を読み込み中…" });
  const micropip = py.pyimport("micropip");
  await micropip.install(new URL("pymupdf-1.28.2-cp313-abi3-pyodide_2025_0_wasm32.whl", self.location).href);
  const pkg = await (await fetch("pdf2mscz.zip")).arrayBuffer();
  py.unpackArchive(pkg, "zip", { extractDir: "/home/pyodide" });
  await py.runPythonAsync(`
import sys, json
sys.path.insert(0, "/home/pyodide")
from pdf2mscz.recognize import recognize
from pdf2mscz.mscx_write import write_mscz
from pdf2mscz import ir

def convert(name):
    score = recognize("/tmp/in.pdf")
    if score["measures"]:
        write_mscz(score, "/tmp/out.mscz")
    return json.dumps(score, ensure_ascii=False)

def from_ir(text, force):
    score = json.loads(text)
    errs = ir.validate(score)
    if errs and not force:
        return json.dumps({"errors": errs}, ensure_ascii=False)
    write_mscz(score, "/tmp/out.mscz")
    return json.dumps({"errors": errs, "score": {"parts": score["parts"], "measures": len(score["measures"])}}, ensure_ascii=False)
`);
  postMessage({ type: "ready" });
}

const ready = init().catch((e) => postMessage({ type: "fatal", text: String(e) }));

onmessage = async (ev) => {
  await ready;
  const msg = ev.data;
  try {
    if (msg.type === "convert") {
      py.FS.writeFile("/tmp/in.pdf", new Uint8Array(msg.data));
      try { py.FS.unlink("/tmp/out.mscz"); } catch (_) {}
      const json = py.globals.get("convert")(msg.name);
      let mscz = null;
      try { mscz = py.FS.readFile("/tmp/out.mscz"); } catch (_) {}
      postMessage({ type: "result", id: msg.id, json, mscz }, mscz ? [mscz.buffer] : []);
    } else if (msg.type === "from_ir") {
      try { py.FS.unlink("/tmp/out.mscz"); } catch (_) {}
      const json = py.globals.get("from_ir")(msg.text, !!msg.force);
      let mscz = null;
      try { mscz = py.FS.readFile("/tmp/out.mscz"); } catch (_) {}
      postMessage({ type: "ir_result", id: msg.id, json, mscz }, mscz ? [mscz.buffer] : []);
    }
  } catch (e) {
    postMessage({ type: "error", id: msg.id, text: String(e) });
  }
};
