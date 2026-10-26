# HITL: 自動変換の「要確認」箇所を Claude Code で直す手順

MuseScore などから書き出したベクター PDF は `pdf2mscz convert` で自動変換できます。
まれに音価の合計が合わない小節が「要確認」として出るので、その小節だけを Claude Code に直させます。

## 使い方 (人間がやること)

```
prompts/hitl_review.md の手順で 〇〇.pdf の要確認箇所を直して
```

---

## Claude Code への指示

1. IR 付きで変換する:
   ```
   pdf2mscz convert <PDF> -o <出力>.mscz --ir
   ```
   `<出力>.json` ができ、要確認箇所 (`warnings`: page / measure / staff / voice / msg) が表示される。警告がなければ終了。
2. 要確認の小節ごとに画像を切り出して見る:
   ```
   pdf2mscz crop <PDF> <小節番号> -o work/m<小節番号>.png
   ```
3. JSON の `measures[小節番号 - 1].staves[五線番号 - 1].voices` を、画像のとおりに書き直す。
   書式は `docs/IR_FORMAT.md` に従う (音高は実音、ト音 8vb は 1 オクターブ下)。
   直した小節の警告は `warnings` から消す。
4. `pdf2mscz validate <出力>.json` でエラーがなくなるまで直す
   (`warnings` 以外にもエラーが出ていたら同じ手順で直す)。
5. `pdf2mscz from-ir <出力>.json -o <出力>.mscz` で書き出す。
6. 直した小節と、直せなかった箇所を短く報告する。
