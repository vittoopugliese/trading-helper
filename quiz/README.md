# Quiz Trading

Standalone quiz built from the repo notes. Open `index.html` with a double click; no server needed.

- Runs of 10 random questions (uniform random, no memory between runs).
- 4-option questions plus true/false. About 20% of the 4-option questions are shown as true/false
  statements (question + one option, true or false).
- Only the chosen answer is colored green or red. The correct answer is never revealed.
- Best run, run count, average and best streak are kept in `localStorage`.
- Keys: `1`–`4` to answer, `V`/`F` (or `1`/`2`) on true/false, `Enter` for next.

## Question bank

`bank/*.js`, one file per group of notes. Each file calls:

```js
QB.add("source-id", [
  ["Question", "correct", "wrong", "wrong", "wrong"],  // options are shuffled at runtime
  ["Statement", true],                                 // true/false
]);
```

A new file must also be added as a `<script>` tag in `index.html` and to the `TOPICS` map (label).
Validate after editing:

```
node quiz/validate.js
```

It checks the format, repeated options, repeated questions and that `index.html` loads every bank file.
