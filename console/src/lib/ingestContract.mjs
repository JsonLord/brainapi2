export function requireAcceptedTask(status, payload) {
  const id = payload?.task_id;
  if (status !== 202 || typeof id !== "string" || ["", "none", "null"].includes(id.trim().toLowerCase())) {
    throw new Error("INGESTION_CONTRACT_ERROR: expected HTTP 202 with a non-empty task_id");
  }
  return id;
}
