// Pins the TypeScript copy of the protocol rules to the numbers asserted in tests/test_git_escrow.py.
import assert from "node:assert/strict";
import test from "node:test";
import { ATTO, fmtGen, parseGen } from "./format";
import { bondFor, disputeBond, disputeFee, MAX_DISPUTES, validateBaseline, validateRepositoryUrl } from "./rules";

test("bond math: 15% of 100 GEN", () => {
  assert.equal(bondFor(100n * ATTO, 1500), 15n * ATTO);
  assert.equal(bondFor(100n * ATTO, 1000), 10n * ATTO);
  assert.equal(bondFor(100n * ATTO, 2000), 20n * ATTO);
});

test("dispute bond doubles per dispute and per strike, with a floor and a cap", () => {
  const base = 1n * ATTO;
  assert.equal(disputeBond(100n * ATTO, 0, 0), base);
  assert.equal(disputeBond(100n * ATTO, 1, 0), 2n * base);
  assert.equal(disputeBond(100n * ATTO, 2, 0), 4n * base);
  assert.equal(disputeBond(100n * ATTO, 0, 1), 2n * base);
  assert.equal(disputeBond(100n * ATTO, 0, 99), 16n * base);
  assert.equal(disputeBond(1n * ATTO, 0, 0), ATTO / 10n);
  assert.equal(MAX_DISPUTES, 3);
});

test("GEN formatting round-trips", () => {
  assert.equal(parseGen("1.5"), 15n * ATTO / 10n);
  assert.equal(fmtGen(1234n * ATTO + ATTO / 2n), "1,234.5");
  assert.throws(() => parseGen("-1"));
});

test("dispute fee is 3% of the reward with a 0.02 GEN floor", () => {
  assert.equal(disputeFee(100n * ATTO), 3n * ATTO);
  assert.equal(disputeFee(ATTO / 10n), ATTO / 50n);
});

test("create-form validation mirrors the contract's repository_url and baseline rules", () => {
  assert.equal(validateRepositoryUrl("https://github.com/acme/widgets"), null);
  for (const bad of ["acme/widgets", "https://github.com/acme/widgets.git", "https://github.com/acme/widgets/", "http://github.com/acme/widgets", "https://gitlab.com/acme/widgets", "https://github.com/a/b/../c"]) {
    assert.ok(validateRepositoryUrl(bad), bad);
  }
  assert.equal(validateBaseline("c".repeat(40)), null);
  assert.equal(validateBaseline(` ${"C".repeat(40)} `), null); // trimmed and lower-cased before sending
  for (const bad of ["", "abc", "g".repeat(40), "c".repeat(39), "c".repeat(41)]) assert.ok(validateBaseline(bad), bad);
});
