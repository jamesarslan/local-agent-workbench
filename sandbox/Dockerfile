FROM node:24-bookworm-slim
ARG OMP_REF=v19.1.0
ARG PI_VERSION=1.6.0
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl git openssh-server python3 ripgrep && rm -rf /var/lib/apt/lists/* && mkdir /run/sshd && rm -f /etc/ssh/ssh_host_*
RUN curl -fsSL https://raw.githubusercontent.com/can1357/oh-my-pi/a7135b8f31826c878734388b648f52bed8da436e/scripts/install.sh -o /tmp/install-omp && PI_INSTALL_DIR=/usr/local/bin sh /tmp/install-omp --binary --ref "$OMP_REF" && rm /tmp/install-omp && omp --version
RUN npm install -g "@earendil-works/pi-coding-agent@$PI_VERSION" && pi --version
RUN mkdir -p /workspace && chown node:node /workspace && printf '%s\n' 'PermitRootLogin no' 'PasswordAuthentication no' 'KbdInteractiveAuthentication no' 'AllowUsers node' 'GatewayPorts no' 'AllowTcpForwarding remote' >> /etc/ssh/sshd_config
WORKDIR /workspace
CMD ["/bin/sh","-ec","ssh-keygen -A; exec /usr/sbin/sshd -D -e"]
