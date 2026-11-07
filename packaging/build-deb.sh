#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 VERSION" >&2
    exit 2
fi

version=$1
case "$version" in
    ""|*[!0-9A-Za-z.+:~_-]*)
        echo "Invalid Debian package version: $version" >&2
        exit 2
        ;;
esac

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_dir=$(dirname -- "$script_dir")
output_dir=${OUTPUT_DIR:-"$project_dir/dist"}
package_root=$(mktemp -d)
trap 'rm -rf "$package_root"' EXIT HUP INT TERM

install -Dm755 "$script_dir/remindme-launcher" "$package_root/usr/lib/remindme/remindme"
install -Dm644 "$project_dir/remindme.py" "$package_root/usr/lib/remindme/remindme.py"
install -Dm644 "$project_dir/assets/banner.txt" "$package_root/usr/lib/remindme/assets/banner.txt"
install -Dm644 "$script_dir/remindme.service" \
    "$package_root/usr/lib/systemd/user/remindme.service"
install -Dm644 "$project_dir/README.md" "$package_root/usr/share/doc/remindme/README.md"
install -Dm644 "$script_dir/debian/control.in" "$package_root/DEBIAN/control"
sed -i "s/@VERSION@/$version/" "$package_root/DEBIAN/control"

mkdir -p "$package_root/usr/bin" "$output_dir"
ln -s ../lib/remindme/remindme "$package_root/usr/bin/remindme"
ln -s remindme "$package_root/usr/bin/remind"
ln -s remindme "$package_root/usr/bin/!remindme"
ln -s remindme "$package_root/usr/bin/!remind"

dpkg-deb --build --root-owner-group "$package_root" \
    "$output_dir/remindme_${version}_all.deb"
echo "Built $output_dir/remindme_${version}_all.deb"
