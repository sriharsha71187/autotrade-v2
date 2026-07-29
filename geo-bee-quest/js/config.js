/* GeoBee Quest configuration.
 *
 * SUPABASE (optional, for syncing progress between devices):
 *   1. In your Supabase project run supabase/schema.sql (SQL Editor → paste → Run).
 *   2. Copy Project Settings → API → "Project URL" and "anon public" key below.
 *   3. Open the app → Settings → Family Sync → create the parent account once,
 *      then sign in on each device.
 * The anon key is designed to be public in frontend code; data access is
 * protected by row-level security (see schema.sql).
 *
 * Leave both blank to run fully offline with device-local progress.
 */
window.GBQ_CONFIG = {
  supabaseUrl: "",      // e.g. "https://abcdefgh.supabase.co"
  supabaseAnonKey: "",  // e.g. "eyJhbGciOi..."
};
