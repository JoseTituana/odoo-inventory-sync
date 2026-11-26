# Odoo 19 Community plus what Ecuadorian SRI electronic invoicing needs:
# the OCA modules l10n_ec_base and l10n_ec_account_edi (AGPL-3) and the
# Python libraries they sign and send documents with.
#
# Build and start (from /opt/odoo):  docker compose build && docker compose up -d
FROM odoo:19

USER root

# Versions are pinned so a rebuild gives the same image. To upgrade the OCA
# modules, change these two lines, rebuild, then update the modules in Odoo.
ARG OCA_L10N_EC_BASE=19.0.1.0.0.2
ARG OCA_L10N_EC_ACCOUNT_EDI=19.0.1.0.0.2

# l10n_ec_account_edi declares these floors in its manifest. The image's
# Ubuntu packages are older (cryptography 41, pyOpenSSL 23), so newer ones are
# installed beside them in /usr/local, which Python loads first.
# --ignore-installed: pip cannot uninstall Ubuntu's copies, and must not.
# packaging: Odoo needs it to read version ranges such as "cryptography>=42"
# in a manifest; without it the module cannot even be installed.
RUN pip3 install --no-cache-dir --break-system-packages --ignore-installed \
        "cryptography>=42,<47" "pyopenssl>=24.1.0" "urllib3>=2.0.7" \
        "xmlsig>=0.1.9" "xades==0.2.4" packaging \
    && pip3 install --no-cache-dir --break-system-packages --no-deps \
        --target /tmp/oca \
        "odoo-addon-l10n-ec-base==${OCA_L10N_EC_BASE}" \
        "odoo-addon-l10n-ec-account-edi==${OCA_L10N_EC_ACCOUNT_EDI}" \
    && mkdir -p /mnt/oca-addons \
    && cp -r /tmp/oca/odoo/addons/l10n_ec_base /tmp/oca/odoo/addons/l10n_ec_account_edi /mnt/oca-addons/ \
    && rm -rf /tmp/oca

USER odoo
