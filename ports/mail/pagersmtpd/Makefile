PORTNAME=	pagersmtpd
DISTVERSION=	0.7.0
CATEGORIES=	mail
MASTER_SITES=	GH

MAINTAINER=	ofloo@ofloo.io
COMMENT=	Receive-only SMTP catch-all that forwards mail to PagerMCP
WWW=		https://github.com/Ofloo/PagerSMTPd

LICENSE=	0BSD
LICENSE_FILE=	${WRKSRC}/LICENSE

USE_GITHUB=	yes
GH_ACCOUNT=	Ofloo
GH_PROJECT=	PagerSMTPd
GH_TAGNAME=	v${DISTVERSION}

USES=		python
RUN_DEPENDS=	${PYTHON_PKGNAMEPREFIX}aiosmtpd>=1.4.6:mail/py-aiosmtpd@${PY_FLAVOR}

NO_ARCH=	yes
NO_BUILD=	yes

PLIST_FILES=	lib/pagersmtpd/smtp_server.py \
		lib/pagersmtpd/VERSION \
		etc/rc.d/pagersmtpd \
		etc/pagersmtpd.env.sample

do-install:
	${MKDIR} ${STAGEDIR}${PREFIX}/lib/pagersmtpd
	${INSTALL_SCRIPT} ${WRKSRC}/smtp_server.py \
		${STAGEDIR}${PREFIX}/lib/pagersmtpd/smtp_server.py
	${INSTALL_DATA} ${WRKSRC}/VERSION \
		${STAGEDIR}${PREFIX}/lib/pagersmtpd/VERSION
	${INSTALL_SCRIPT} ${FILESDIR}/pagersmtpd \
		${STAGEDIR}${PREFIX}/etc/rc.d/pagersmtpd
	${INSTALL_DATA} ${FILESDIR}/pagersmtpd.env.sample \
		${STAGEDIR}${PREFIX}/etc/pagersmtpd.env.sample

.include <bsd.port.mk>
