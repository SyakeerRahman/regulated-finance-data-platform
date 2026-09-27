# Put and get one object in an S3-compatible bucket: AWS S3, Cloudflare R2 or MinIO.
# Sourced by backup.sh and restore.sh.
#
# curl signs the request itself (--aws-sigv4, curl 7.75 or later; Ubuntu 24.04 has 8.5). An AWS
# CLI image would cost about 400 MB of an 8 GB disk budget to send two files a night.
#
# Needs in .env: BACKUP_ENDPOINT, BACKUP_BUCKET, BACKUP_REGION, BACKUP_ACCESS_KEY_ID,
# BACKUP_SECRET_ACCESS_KEY. R2 uses the region "auto".

require_backup_settings() {
    local name missing=0
    for name in BACKUP_ENDPOINT BACKUP_BUCKET BACKUP_REGION BACKUP_ACCESS_KEY_ID BACKUP_SECRET_ACCESS_KEY; do
        if [ -z "${!name:-}" ]; then
            echo "missing in .env: ${name}" >&2
            missing=1
        fi
    done
    return "${missing}"
}

# Path-style URLs (endpoint/bucket/key). S3, R2 and MinIO all accept them.
s3_url() { echo "${BACKUP_ENDPOINT%/}/${BACKUP_BUCKET}/$1"; }

s3_curl() {
    curl --silent --show-error --fail \
        --aws-sigv4 "aws:amz:${BACKUP_REGION}:s3" \
        --user "${BACKUP_ACCESS_KEY_ID}:${BACKUP_SECRET_ACCESS_KEY}" \
        "$@"
}

# s3_put <local file> <key>
s3_put() { s3_curl --upload-file "$1" "$(s3_url "$2")"; }

# s3_get <key> <local file>
s3_get() { s3_curl --output "$2" "$(s3_url "$1")"; }
