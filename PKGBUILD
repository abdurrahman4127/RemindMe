pkgname=remindme
pkgver=1.0.0
pkgrel=1
pkgdesc='Local-first terminal reminder app'
arch=('any')
url='https://github.com/abdurrahman4127/RemindMe'
depends=('python>=3.10')
optdepends=('libnotify: desktop notifications via notify-send'
            'systemd: user notification service')
source=()
sha256sums=()

package() {
    install -Dm755 "$startdir/packaging/remindme-launcher" \
        "$pkgdir/usr/lib/remindme/remindme"
    install -Dm644 "$startdir/remindme.py" "$pkgdir/usr/lib/remindme/remindme.py"
    install -Dm644 "$startdir/assets/banner.txt" \
        "$pkgdir/usr/lib/remindme/assets/banner.txt"
    install -Dm644 "$startdir/packaging/remindme.service" \
        "$pkgdir/usr/lib/systemd/user/remindme.service"
    install -Dm644 "$startdir/README.md" \
        "$pkgdir/usr/share/doc/remindme/README.md"

    install -d "$pkgdir/usr/bin"
    ln -s ../lib/remindme/remindme "$pkgdir/usr/bin/remindme"
    ln -s remindme "$pkgdir/usr/bin/remind"
    ln -s remindme "$pkgdir/usr/bin/!remindme"
    ln -s remindme "$pkgdir/usr/bin/!remind"
}
