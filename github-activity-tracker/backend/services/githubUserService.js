const githubClient = require('../utils/githubClient');
const pool = require('../db/dbPool');
const {
  NAME_FETCH_DELAY_MS,
  NAME_BACKFILL_BATCH_SIZE,
  NAME_BACKFILL_MAX_BATCH_SIZE,
  NAME_FETCH_RETRY_MS,
  NAME_REFRESH_INTERVAL_MS,
} = require('../config/syncConfig');

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isBotType(type) {
  return String(type || '').toLowerCase() === 'bot';
}

function normalizeDisplayName(name) {
  if (!name || !String(name).trim()) {
    return null;
  }

  return String(name).trim();
}

/**
 * Fetch GitHub profile display name for a login. Returns null when unset or on failure.
 */
async function fetchGitHubUserName(login) {
  if (!login) return null;

  try {
    const { data } = await githubClient.get(`/users/${encodeURIComponent(login)}`);
    const name = data?.name;
    return name && String(name).trim() ? String(name).trim() : null;
  } catch (error) {
    const status = error?.response?.status;
    if (status === 404) {
      console.warn(`GitHub user not found while fetching name: ${login}`);
      return null;
    }
    console.warn(`Failed to fetch GitHub name for ${login}:`, error.message);
    return null;
  }
}

/**
 * Resolve display name: use provided value, re-fetch from GitHub if stale, or use stored value.
 */
async function resolveGitHubUserName({ github_user_id, login, type, name }) {
  const providedName = normalizeDisplayName(name);
  if (providedName) {
    return providedName;
  }

  if (!login || isBotType(type)) {
    return null;
  }

  const existing = await pool.query(
    `
      SELECT name, updated_at
      FROM github_users
      WHERE github_user_id = $1
    `,
    [github_user_id]
  );

  const storedName = normalizeDisplayName(existing.rows[0]?.name);
  const updatedAt = existing.rows[0]?.updated_at;

  if (storedName) {
    const isStale = updatedAt && (Date.now() - new Date(updatedAt).getTime()) > NAME_REFRESH_INTERVAL_MS;
    if (isStale) {
      const freshName = await fetchGitHubUserName(login);
      return freshName || storedName;
    }
    return storedName;
  }

  return fetchGitHubUserName(login);
}

/**
 * Ensure a brand-new GitHub user has an initial active user_details row.
 * Do not recreate an active row when the user already has assignment history
 * (including deactivated rows); otherwise sync would undo explicit deactivation.
 */
async function ensureActiveUserDetails(userId) {
  await pool.query(
    `
      INSERT INTO user_details (user_id, role_id, active, active_from, active_to)
      SELECT $1, NULL, true, '1970-01-01'::timestamp, NULL
      WHERE NOT EXISTS (
        SELECT 1
        FROM user_details ud
        WHERE ud.user_id = $1
      )
    `,
    [userId]
  );
}

/**
 * Upsert a GitHub user and return the internal github_users.id.
 */
async function upsertGitHubUser({
  github_user_id,
  login,
  avatar_url,
  html_url,
  type,
  name,
}) {
  const displayName = await resolveGitHubUserName({
    github_user_id,
    login,
    type,
    name,
  });

  const userResult = await pool.query(
    `
      INSERT INTO github_users (github_user_id, login, avatar_url, html_url, type, name)
      VALUES ($1, $2, $3, $4, $5, $6)
      ON CONFLICT (github_user_id)
      DO UPDATE SET
        login = EXCLUDED.login,
        avatar_url = EXCLUDED.avatar_url,
        html_url = EXCLUDED.html_url,
        type = EXCLUDED.type,
        name = COALESCE(EXCLUDED.name, github_users.name),
        updated_at = CURRENT_TIMESTAMP
      RETURNING id
    `,
    [
      github_user_id,
      login,
      avatar_url || null,
      html_url || null,
      type || null,
      displayName,
    ]
  );

  const userId = userResult.rows[0].id;
  await ensureActiveUserDetails(userId);
  return userId;
}

/**
 * Backfill profile names for users that do not have one stored in github_users yet.
 * Processes a bounded batch per call; one failed user does not abort the batch.
 */
async function backfillMissingUserNames({ limit } = {}) {
  const parsedLimit = Number.parseInt(limit, 10);
  const batchSize = Math.min(
    parsedLimit > 0 ? parsedLimit : NAME_BACKFILL_BATCH_SIZE,
    NAME_BACKFILL_MAX_BATCH_SIZE
  );

  const result = await pool.query(
    `
      SELECT u.id, u.login, u.type, u.github_user_id
      FROM github_users u
      WHERE u.name IS NULL
        AND u.login IS NOT NULL
        AND (u.type IS NULL OR LOWER(u.type) <> 'bot')
        AND u.updated_at <= NOW() - ($2::int * interval '1 millisecond')
      ORDER BY u.updated_at ASC, u.id ASC
      LIMIT $1
    `,
    [batchSize, NAME_FETCH_RETRY_MS]
  );

  let namesFetched = 0;
  let errors = 0;

  for (const user of result.rows) {
    try {
      const name = await fetchGitHubUserName(user.login);

      if (name) {
        await pool.query(
          `
            UPDATE github_users
            SET name = $1, updated_at = CURRENT_TIMESTAMP
            WHERE id = $2
          `,
          [name, user.id]
        );
        namesFetched += 1;
      } else {
        await pool.query(
          `
            UPDATE github_users
            SET updated_at = CURRENT_TIMESTAMP
            WHERE id = $1
          `,
          [user.id]
        );
      }

      await ensureActiveUserDetails(user.id);
    } catch (error) {
      errors += 1;
      console.error(`Failed to backfill user ${user.login}:`, error.message);
      try {
        await pool.query(
          `
            UPDATE github_users
            SET updated_at = CURRENT_TIMESTAMP
            WHERE id = $1
          `,
          [user.id]
        );
      } catch (updateError) {
        console.error(`Failed to defer retry for user ${user.login}:`, updateError.message);
      }
    }

    await sleep(NAME_FETCH_DELAY_MS);
  }

  return {
    users_checked: result.rows.length,
    names_fetched: namesFetched,
    errors,
  };
}

module.exports = {
  fetchGitHubUserName,
  upsertGitHubUser,
  backfillMissingUserNames,
  ensureActiveUserDetails,
};
