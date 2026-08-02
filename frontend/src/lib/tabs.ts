/** Whether one more tab may be opened, given the environment's cap on
 * simultaneous open tabs (0 = unlimited, set by an admin per environment). */
export function canOpenTab(cap: number, currentOpenCount: number): boolean {
  return cap <= 0 || currentOpenCount < cap;
}
