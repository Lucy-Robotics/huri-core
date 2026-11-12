import logging
import os

from ray.serve import Application

from huri_core.core.compat import check_actuation, check_manifests, check_modules
from huri_core.core.factory import bind_deployment_handles
from huri_core.core.huri import HuRI
from huri_core.core.plugins import (
    find_manifest,
    load_event_policy,
    load_events,
    load_modules,
    module_distribution,
    module_label,
    parse_selection,
)

logger = logging.getLogger("ray.serve")


def build_app() -> Application:
    # HURI_MODULES="stt,rag,tts" restricts which modules this server registers —
    # and therefore which Serve deployments bind_deployment_handles() builds, so
    # a host that cannot run e.g. CosyVoice never creates the TTS replica. It may
    # also list import paths (my_pkg.servo:Servo) of unpackaged modules.
    # Default-deny: unset or empty registers no module at all, because any
    # installed package can provide one. huri-modules' installer and serve
    # configs set it.
    modules = load_modules(parse_selection(os.environ.get("HURI_MODULES")))
    distributions = {name: module_distribution(name) for name in modules}
    if modules:
        logger.info(
            "[HuRI] enabled modules (HURI_MODULES): %s",
            ", ".join(module_label(n, *distributions[n]) for n in sorted(modules)),
        )
    else:
        logger.warning(
            "[HuRI] HURI_MODULES is unset or empty: no module is enabled and every "
            "session will be rejected. Set it, e.g. HURI_MODULES=vad,stt,tag,qag,rag"
        )
    events = load_events()
    # Fail here, before anything is deployed, rather than in a replica.
    check_modules(modules, events)
    manifests = {}
    for name in modules:
        manifest = find_manifest(name)
        if manifest is not None:
            manifests[name] = manifest
    check_manifests(modules, manifests, events)
    # Modules that can move hardware need their own, explicit opt-in.
    opted_in = parse_selection(os.environ.get("HURI_ACTUATION_MODULES"))
    actuating = check_actuation(modules, manifests, opted_in)
    unused = sorted(set(opted_in) - set(actuating))
    if unused:
        logger.warning(
            "[HuRI] HURI_ACTUATION_MODULES lists %s, which no enabled module "
            "declaring actuation matches (typo, or not in HURI_MODULES?)",
            unused,
        )
    if actuating:
        logger.warning(
            "[HuRI] ACTUATION ENABLED: %s can drive physical hardware "
            "(opted in by HURI_ACTUATION_MODULES)",
            actuating,
        )
    # Unknown or broken policy: refuse to start, before anything is deployed.
    policy_name = os.environ.get("HURI_EVENT_POLICY") or "interrupt"
    event_policy = load_event_policy(policy_name)
    logger.info("[HuRI] event policy: %s (%s)", policy_name, event_policy.__name__)
    handles = bind_deployment_handles(modules)
    app: Application = HuRI.bind(  # type: ignore[attr-defined]
        modules, handles, events, actuating, distributions, event_policy
    )
    return app


app = build_app()
