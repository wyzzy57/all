# TensorBoard Deep Analysis Design

## Goal

Keep VisioX focused on its native training workflow while delegating computation
graph and parameter-distribution inspection to TensorBoard. Users open the
TensorBoard view for the currently selected training job from VisioX instead of
using separate native graph and histogram pages.

## Product Scope

VisioX continues to provide native views for training status, scalar metrics,
resource usage, logs, artifacts, and model outputs.

The native `计算图` and `直方图` tabs are removed. Their chart components,
frontend state, and VisioX-only graph and histogram API contracts are removed
when they have no remaining consumers.

The training visualization page keeps its existing top-right advanced-tools
menu and its existing `TensorBoard` command. No second TensorBoard command is
added to the tabs or content area. The command opens TensorBoard in a new browser
tab, and the VisioX page remains open.

## Data and Service Boundaries

Training workers continue writing TensorBoard event data required for:

- scalar metrics;
- the model graph;
- weight histograms;
- gradient histograms when supported by the installed training stack;
- images and other TensorBoard-compatible summaries.

MLflow tracking and VisioX progress telemetry remain unchanged. Removing the
native graph and histogram views must not remove TensorBoard summary generation
or event files.

TensorBoard remains a separately managed internal service. Persisted run-name
metadata remains available to TensorBoard and future integrations, but the
existing top-right command continues to open the configured TensorBoard service
without adding a second run-specific navigation path.

## Navigation and Security

The frontend reuses its configured TensorBoard URL and existing explicit menu
action. The browser opens the URL with an isolated new-window context.

TensorBoard is reached through the VisioX reverse proxy or gateway. Its raw
service port is not exposed as a customer-facing endpoint. The gateway applies
the same authenticated user and pipeline-access checks used by the training-job
page before forwarding the request.

If TensorBoard is not configured, the existing advanced-tools menu keeps its
current unavailable state. No graph- or histogram-specific fallback panel is
shown.

## Cleanup

Remove only VisioX-native graph and histogram presentation code and APIs. Keep:

- TensorBoard service configuration;
- event-file mounts and storage;
- worker summary writers and instrumentation;
- persisted run-name metadata;
- scalar data used by existing native metric charts.

The currently uncommitted direct TensorBoard iframe experiment is discarded. No
TensorBoard page is embedded inside the VisioX content area.

## Verification

- Frontend tests confirm that graph and histogram tabs are absent.
- Frontend tests confirm that the existing top-right `TensorBoard` action remains
  available and opens the configured URL in a new tab.
- Worker tests confirm TensorBoard graph and histogram summaries are still
  emitted.
- A local end-to-end check opens TensorBoard for a completed two-epoch training
  job and confirms the selected run contains its graph and histogram data.
