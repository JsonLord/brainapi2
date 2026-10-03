import assert from "node:assert/strict";
import test from "node:test";
import {
  accessibleWorkspaceTabs,
  resolveRoutedWorkspace,
  scopedRequestBrainId,
  switchWorkspacePath,
  workspaceCacheKey,
  workspacePath,
} from "./workspaceRoutes.ts";

const alpha = { slug: "alpha", brain_id: "alpha", archived: false };
const beta = { slug: "beta", brain_id: "beta", archived: false };

test("route resolves the exact workspace", () => {
  assert.equal(resolveRoutedWorkspace([alpha, beta], "beta"), beta);
});

test("workspace switch preserves the active subpage", () => {
  assert.equal(switchWorkspacePath("/w/alpha/graph", "beta"), "/w/beta/graph");
});

test("workspace switching is represented as navigation data, not reload", () => {
  assert.equal(switchWorkspacePath("/w/alpha/tasks", "beta"), "/w/beta/tasks");
  assert.equal(switchWorkspacePath.toString().includes("location.reload"), false);
});

test("system PAT sees multiple workspaces", () => {
  assert.deepEqual(accessibleWorkspaceTabs([alpha, beta], true), [alpha, beta]);
});

test("per-brain PAT remains locked to its brain", () => {
  assert.deepEqual(accessibleWorkspaceTabs([alpha, beta], false, "alpha"), [alpha]);
});

test("unknown route does not fall back to another workspace", () => {
  assert.equal(resolveRoutedWorkspace([alpha, beta], "missing"), null);
});

test("created workspace has a canonical overview navigation target", () => {
  assert.equal(workspacePath("new-space"), "/w/new-space/");
});

test("API request scope comes from the active workspace brain", () => {
  assert.equal(scopedRequestBrainId(beta), "beta");
});

test("workspace cache keys differ across brains", () => {
  assert.notEqual(workspaceCacheKey("graph", "alpha"), workspaceCacheKey("graph", "beta"));
});
