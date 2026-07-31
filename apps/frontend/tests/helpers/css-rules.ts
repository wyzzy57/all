import { parse as parseSfc } from "@vue/compiler-sfc";
import postcss, { type Declaration, type Rule } from "postcss";

export type CssDeclarations = ReadonlyMap<string, readonly string[]>;
export type CssSourceKind = "css" | "sfc";

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

function styleBlocks(source: string) {
  const { descriptor, errors } = parseSfc(source, { filename: "raw-component.vue" });
  if (errors.length > 0) {
    throw new Error(`could not parse component styles: ${errors.map(String).join(", ")}`);
  }
  return descriptor.styles.map((style) => style.content);
}
