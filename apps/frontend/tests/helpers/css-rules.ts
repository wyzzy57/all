import { parse as parseSfc } from "@vue/compiler-sfc";
import postcss, { type Declaration, type Rule } from "postcss";

export type CssDeclarations = ReadonlyMap<string, readonly string[]>;
export type CssSourceKind = "css" | "sfc";

const MAX_SHADOW_OFFSET_PX = 2;
const MAX_SHADOW_BLUR_PX = 6;
const MAX_SHADOW_SPREAD_PX = 1;
const NON_TRANSLATING_TRANSFORMS = new Set([
  "perspective",
  "rotate", "rotate3d", "rotatex", "rotatey", "rotatez",
  "scale", "scale3d", "scalex", "scaley", "scalez",
  "skew", "skewx", "skewy",
]);

type LiftStatus = "safe" | "lift" | "unknown";

export function topLevelRuleDeclarations(
  source: string,
  selector: string,
  sourceKind: CssSourceKind = "css",
): CssDeclarations | undefined {
  const cssSources = sourceKind === "sfc" ? styleBlocks(source) : [source];
  const matchingRules = cssSources.flatMap((css) => postcss.parse(css).nodes.filter(
    (node): node is Rule => node.type === "rule" && node.selector.trim() === selector,
  ));

  if (matchingRules.length > 1) {
    throw new Error(`expected at most one top-level ${selector} rule, found ${matchingRules.length}`);
  }
  if (matchingRules.length === 0) return undefined;

  const declarations = new Map<string, string[]>();
  for (const node of matchingRules[0]!.nodes) {
    if (node.type !== "decl") continue;
    const declaration = node as Declaration;
    const values = declarations.get(declaration.prop) ?? [];
    values.push(declaration.value.trim());
    declarations.set(declaration.prop, values);
  }
  return declarations;
}

export function hasForbiddenHoverElevation(declarations: CssDeclarations | undefined): boolean {
  if (!declarations) return false;

  const hasLift = (declarations.get("transform") ?? []).some(transformIsForbidden)
    || (declarations.get("translate") ?? []).some(translatePropertyIsForbidden);
  const hasLargeShadow = (declarations.get("box-shadow") ?? []).some((value) => {
    if (value.trim().toLowerCase() === "none") return false;
    return splitTopLevel(value, ",").some((shadow) => !isSmallShadow(shadow));
  });
  return hasLift || hasLargeShadow;
}

function styleBlocks(source: string) {
  const { descriptor, errors } = parseSfc(source, { filename: "raw-component.vue" });
  if (errors.length > 0) {
    throw new Error(`could not parse component styles: ${errors.map(String).join(", ")}`);
  }
  return descriptor.styles.map((style) => style.content);
}

function transformIsForbidden(value: string) {
  if (value.trim().toLowerCase() === "none") return false;
  const calls = topLevelFunctionCalls(value);
  if (!calls) return true;
  return calls.some((call) => transformCallLiftStatus(call) !== "safe");
}

function translatePropertyIsForbidden(value: string) {
  if (value.trim().toLowerCase() === "none") return false;
  const values = splitTopLevelWhitespace(value);
  if (values.length < 1 || values.length > 3) return true;
  const statuses = values.map(lengthLiftStatus);
  if (statuses.includes("unknown")) return true;
  return statuses[1] === "lift";
}

function transformCallLiftStatus(call: { name: string; arguments: string }): LiftStatus {
  if (NON_TRANSLATING_TRANSFORMS.has(call.name)) return "safe";

  const values = functionArguments(call.arguments);
  if (call.name === "translatey") return fixedArityLiftStatus(values, 1, 0, lengthLiftStatus);
  if (call.name === "translate") {
    if (values.length < 1 || values.length > 2) return "unknown";
    if (values.some((value) => lengthLiftStatus(value) === "unknown")) return "unknown";
    return values[1] ? lengthLiftStatus(values[1]) : "safe";
  }
  if (call.name === "translate3d") return fixedArityLiftStatus(values, 3, 1, lengthLiftStatus);
  if (call.name === "matrix") return fixedArityLiftStatus(values, 6, 5, numberLiftStatus);
  if (call.name === "matrix3d") return fixedArityLiftStatus(values, 16, 13, numberLiftStatus);
  return "unknown";
}

function fixedArityLiftStatus(
  values: string[],
  arity: number,
  yIndex: number,
  classify: (value: string | undefined) => LiftStatus,
): LiftStatus {
  if (values.length !== arity) return "unknown";
  const statuses = values.map(classify);
  if (statuses.includes("unknown")) return "unknown";
  return statuses[yIndex] ?? "unknown";
}

function isSmallShadow(value: string) {
  const lengths = splitTopLevelWhitespace(value)
    .map(parseLength)
    .filter((length): length is number | null => length !== undefined);
  if (lengths.length < 2 || lengths.some((length) => length === null)) return false;

  const [offsetX, offsetY, blur = 0, spread = 0] = lengths as number[];
  return Math.abs(offsetX!) <= MAX_SHADOW_OFFSET_PX
    && Math.abs(offsetY!) <= MAX_SHADOW_OFFSET_PX
    && blur! >= 0
    && blur! <= MAX_SHADOW_BLUR_PX
    && Math.abs(spread!) <= MAX_SHADOW_SPREAD_PX;
}

function parseLength(token: string): number | null | undefined {
  const match = token.match(/^(-?(?:\d+(?:\.\d+)?|\.\d+))([a-z%]*)$/i);
  if (!match) return undefined;
  const value = Number(match[1]);
  const unit = match[2]!.toLowerCase();
  if (value === 0 || unit === "px") return value;
  return null;
}

function lengthLiftStatus(value: string | undefined): LiftStatus {
  if (!value) return "unknown";
  const normalized = value.trim();
  const calcMatch = normalized.match(/^calc\(\s*([^()]+)\s*\)$/i);
  return numericLiftStatus(calcMatch?.[1] ?? normalized, true);
}

function numberLiftStatus(value: string | undefined): LiftStatus {
  return value ? numericLiftStatus(value.trim(), false) : "unknown";
}

function numericLiftStatus(value: string, allowUnit: boolean): LiftStatus {
  const unit = allowUnit ? "(?:[a-z%]+)?" : "";
  const match = value.match(new RegExp(`^(-?(?:\\d+(?:\\.\\d+)?|\\.\\d+))${unit}$`, "i"));
  if (!match) return "unknown";
  return Number(match[1]) < 0 ? "lift" : "safe";
}

function functionArguments(value: string) {
  const commaSeparated = splitTopLevel(value, ",");
  return commaSeparated.length > 1 ? commaSeparated : splitTopLevelWhitespace(value);
}

function topLevelFunctionCalls(value: string) {
  const calls: Array<{ name: string; arguments: string }> = [];
  let index = 0;
  while (index < value.length) {
    while (/\s/.test(value[index] ?? "")) index += 1;
    if (index >= value.length) break;
    const match = value.slice(index).match(/^([a-z][a-z0-9-]*)\s*\(/i);
    if (!match) return undefined;
    const start = index + match[0].length;
    let depth = 1;
    let end = start;
    while (end < value.length && depth > 0) {
      if (value[end] === "(") depth += 1;
      if (value[end] === ")") depth -= 1;
      end += 1;
    }
    if (depth !== 0) return undefined;
    calls.push({ name: match[1]!.toLowerCase(), arguments: value.slice(start, end - 1) });
    index = end;
  }
  return calls.length > 0 ? calls : undefined;
}

function splitTopLevel(value: string, separator: string) {
  const parts: string[] = [];
  let depth = 0;
  let start = 0;
  for (let index = 0; index < value.length; index += 1) {
    if (value[index] === "(") depth += 1;
    if (value[index] === ")") depth -= 1;
    if (value[index] === separator && depth === 0) {
      parts.push(value.slice(start, index).trim());
      start = index + 1;
    }
  }
  parts.push(value.slice(start).trim());
  return parts.filter(Boolean);
}

function splitTopLevelWhitespace(value: string) {
  const parts: string[] = [];
  let depth = 0;
  let start = -1;
  for (let index = 0; index <= value.length; index += 1) {
    const character = value[index];
    if (character === "(") depth += 1;
    if (character === ")") depth -= 1;
    const boundary = index === value.length || (/\s/.test(character ?? "") && depth === 0);
    if (start !== -1 && boundary) {
      parts.push(value.slice(start, index));
      start = -1;
    } else if (start === -1 && !boundary) {
      start = index;
    }
  }
  return parts;
}
