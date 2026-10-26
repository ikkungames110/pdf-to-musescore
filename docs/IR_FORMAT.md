# 中間表現 (IR) 仕様 `pdf2mscz-ir/1`

PDF 解析器・HITL (Claude Code による読み取り)・`.mscz` 書き出しの共通フォーマットです。
JSON 1 ファイルで楽譜 1 曲を表します。`pdf2mscz validate` で検査、`pdf2mscz from-ir` で `.mscz` にできます。
既存の `.mscz` を `pdf2mscz mscz-to-ir` で IR にすると、お手本として見られます。

## 全体

```json
{
  "format": "pdf2mscz-ir/1",
  "meta": {"title": "瞳", "subtitle": "", "composer": "aiko\nArranged By ...", "lyricist": ""},
  "parts": [
    {"name": "1st", "longName": "1st\nさもちゃん", "instrument": "piano", "staves": 1},
    {"name": "V.Per", "longName": "V.Per", "instrument": "drumset", "staves": 1}
  ],
  "measures": [ { ...小節... }, ... ]
}
```

- `parts[].instrument`: `"piano"` (音高のあるパート。歌のパートもこれで可) か `"drumset"` (ドラム譜)。
- `parts[].staves`: そのパートの五線の数 (ピアノの大譜表なら 2)。通常は 1。
- 任意: `"spatium"` (五線の間隔 mm)、`"warnings"` (解析器が出す要確認リスト。書き出しでは無視される)。

## 小節

```json
{
  "timeSig": "4/4",
  "key": -2,
  "tempo": {"bpm": 63, "text": "<sym>metNoteQuarterUp</sym> = 63"},
  "rehearsal": "1A",
  "staves": [ {五線 1}, {五線 2}, ... ]
}
```

| キー | 意味 |
|---|---|
| `timeSig` | 拍子。**1 小節目は必須**。以降は変わる小節だけ書く |
| `key` | 調号。シャープの数 (+) / フラットの数 (-)。ハ長調は 0。1 小節目は必須、以降は変わる小節だけ |
| `keyAt` | 小節の途中で調が変わる時の位置 (例 `"3/4"`) |
| `len` | 実際の長さが拍子と違う小節 (弱起など)。例 `"1/4"` |
| `tempo` | テンポ。`bpm` は 4 分音符基準 |
| `rehearsal` | 練習番号 |
| `barline` | 小節末の小節線 `"double"` / `"end"` |
| `repeatStart` / `repeatEnd` | 反復記号 |
| `lineBreak` | この小節の後で改段 (元 PDF のレイアウトを保つため) |
| `staves` | **五線の数ぶん、上から順に**並べる (全パートの五線の合計数と一致させる) |

## 五線 (staves の要素)

```json
{
  "clef": "G8vb",
  "harmony": [{"at": "0", "name": "B♭sus4"}, {"at": "1/2", "name": "B♭"}],
  "changes": [{"at": "1/2", "clef": "F"}],
  "voices": [ [イベント...], [声部 2 のイベント...] ]
}
```

- `clef`: 小節頭の音部記号 (1 小節目と、変わる時だけ)。
  `G` ト音 / `G8vb` 下に 8 の付いたト音 / `G8va` / `F` ヘ音 / `F8vb` / `C3` アルト / `C4` テノール / `PERC` 打楽器
- `harmony`: コードネーム。`at` は小節頭からの位置 (全音符 = 1 の分数。4/4 の 3 拍目なら `"1/2"`)
- `changes`: 小節途中の音部記号・調号の変更
- `voices`: 声部ごとのイベント列。普通は 1 声部 `[[...]]`。2 声部目は符尾が下向きの声部

## イベント

### 音符・和音
```json
{"type": "chord", "duration": "eighth", "dots": 1,
 "notes": [{"pitch": "Bb4", "tie": true}, {"pitch": "D5"}],
 "lyrics": [{"text": "fu", "syllabic": "begin", "verse": 1, "melisma": "1/4"}]}
```
- `duration`: `whole` `half` `quarter` `eighth` `16th` `32nd` `64th` (`breve` も可)
- `dots`: 付点の数 (省略 = 0)
- `notes[].pitch`: **実音**の音名 + オクターブ。C4 = 中央ド。`#` / `b` / `##` / `bb`。例 `F#4` `Bb3`
  - ト音記号 8vb (下に小さい 8) の五線では、**見た目より 1 オクターブ下**を書く (第 3 線の B → `B3`)
  - 調号・臨時記号 (小節内で持続)・タイで続く音の臨時記号をすべて反映した実際の音
- `notes[].tie`: 次の音符へタイでつながる
- `notes[].drum`: ドラム譜では `pitch` の代わりに General MIDI 番号 (例: バスドラ 35、スネア 38、クローズドハイハット 42、オープンハイハット 46、クラッシュ 49、ライド 51、ロータム 45、フロアタム 41)
- `lyrics`: 歌詞。`syllabic` は `single`(省略可) / `begin` / `middle` / `end` (ハイフンでつながる音節)。
  `verse` は 2 番以降の歌詞のみ。`melisma` は伸ばし線の長さ (最後に覆う音符までの距離、全音符 = 1)

### 休符
```json
{"type": "rest", "duration": "quarter"}
{"type": "rest", "duration": "measure"}
```
`measure` は小節全体の休符 (全休符の見た目)。見えない休符は `"visible": false`。

### 連符
```json
{"type": "tuplet", "actual": 3, "normal": 2,
 "events": [{"type": "chord", "duration": "eighth", "notes": [{"pitch": "E4"}]}, ...]}
```
3 連符は actual=3, normal=2。中のイベントは見た目どおりの音価 (3 連 8 分なら `eighth`) で書く。

## 長さのルール

各声部の長さの合計 (付点・連符を反映) は小節の長さ (`len` があればそれ、なければ拍子) と一致させる。
2 声部目以降は短くてもよい (残りは休符扱い)。`pdf2mscz validate` で確認できる。
