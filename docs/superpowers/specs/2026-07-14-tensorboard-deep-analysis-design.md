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

The training visualization page exposes one clearly labeled `打开 TensorBoard`
command. It opens TensorBoard in a new browser tab and selects the current
training run. The VisioX page remains open so users can return without losing
their current job selection.

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

TensorBoard remains a separately managed internal service. VisioX identifies the
TensorBoard run from persisted training-job observability metadata. The link must
not guess a run from the pipeline name or from the current list position.

## Navigation and Security

The frontend obtains a VisioX-owned TensorBoard URL for the selected training
job. The URL targets the job's run and opens with `target="_blank"` and
`rel="noopener noreferrer"`.

TensorBoard is reached through the VisioX reverse proxy or gateway. Its raw
service port is not exposed as a customer-facing endpoint. The gateway applies
the same authenticated user and pipeline-access checks used by the training-job
page before forwarding the request.

If TensorBoard or the selected run is unavailable, VisioX keeps the current page
usable and shows a concise failure message instead of opening a blank tab.

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
- Frontend tests confirm that `打开 TensorBoard` is shown for a selected training
  job and opens the run-specific URL in a new tab.
- API tests confirm that users cannot obtain a TensorBoard URL for a training job
  they cannot access.
- Worker tests confirm TensorBoard graph and histogram summaries are still
  emitted.
- A local end-to-end check opens TensorBoard for a completed two-epoch training
  job and confirms the selected run contains its graph and histogram data.
