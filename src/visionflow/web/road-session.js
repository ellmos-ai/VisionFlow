// Selection is a hint only. The authenticated backend owns every case and deadline.
const SELECTED = "visionflow.road.selected";

export function rememberRoadCase(caseId) {
  try {
    if (caseId) sessionStorage.setItem(SELECTED, caseId);
    else sessionStorage.removeItem(SELECTED);
  } catch (_error) { /* Recovery still works when browser storage is unavailable. */ }
}

export async function restoreRoadCases(apiClient) {
  let remembered = null;
  try { remembered = sessionStorage.getItem(SELECTED); } catch (_error) { /* private mode */ }
  const payload = await apiClient.get("/road/cases");
  if (!payload || !Array.isArray(payload.cases) || payload.cases.some(row =>
    !row || typeof row.case_id !== "string" || typeof row.source_id !== "string")) {
    throw new Error("Invalid case recovery response");
  }
  const cases = payload.cases;
  const selectedCaseId = cases.some(row => row.case_id === remembered)
    ? remembered : cases.at(-1)?.case_id || null;
  const sessionLost = Boolean(remembered && !cases.some(row => row.case_id === remembered));
  rememberRoadCase(selectedCaseId);
  return { cases, selectedCaseId, sessionLost };
}
