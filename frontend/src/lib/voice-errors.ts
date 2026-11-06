import { DeviceError, RTVIError } from "@pipecat-ai/client-js";

/** Turn anything thrown while starting a call into a message a customer can act on. */
export async function describeConnectError(error: unknown): Promise<string> {
  // makeRequest() rejects with the raw fetch Response on non-2xx answers.
  if (typeof Response !== "undefined" && error instanceof Response) {
    const detail = await readDetail(error);
    if (error.status === 503) return detail ?? "All voice lines are busy. Please try again shortly.";
    if (error.status === 422 || error.status === 400) {
      return "The voice connection could not be negotiated. Please refresh and try again.";
    }
    return `The voice server returned an error (HTTP ${error.status}).`;
  }
  if (error instanceof DeviceError) return describeDeviceError(error);
  if (error instanceof RTVIError && error.status === 503) {
    return "All voice lines are busy. Please try again shortly.";
  }
  if (error instanceof TypeError) {
    return "Could not reach the voice server. Make sure the backend is running.";
  }
  // The SmallWebRTC transport reports failed offers as "WebRTC offer rejected with status N".
  const status = error instanceof Error ? /status (\d{3})/.exec(error.message)?.[1] : undefined;
  if (status === "503") return "All voice lines are busy. Please try again shortly.";
  if (status === "404") {
    return "The voice service was not found (HTTP 404). The backend on BACKEND_URL (default port 8000) is not running the latest code. Restart it.";
  }
  if (status === "400" || status === "422") {
    return "The voice connection could not be negotiated. Please refresh and try again.";
  }
  if (status) return `The voice server returned an error (HTTP ${status}).`;
  if (error instanceof Error && error.message) return error.message;
  return "Something went wrong while starting the call.";
}

export function describeDeviceError(error: DeviceError): string {
  switch (error.type) {
    case "permissions":
      return "Microphone access is blocked. Allow microphone access for this site and try again.";
    case "not-found":
      return "No microphone was found. Connect a microphone and try again.";
    case "in-use":
      return "Your microphone is being used by another application.";
    case "undefined-mediadevices":
      return "This browser cannot access a microphone here. Use Chrome or Edge on localhost.";
    default:
      return "There was a problem accessing your microphone.";
  }
}

async function readDetail(response: Response): Promise<string | null> {
  try {
    const body = (await response.clone().json()) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : null;
  } catch {
    return null;
  }
}
