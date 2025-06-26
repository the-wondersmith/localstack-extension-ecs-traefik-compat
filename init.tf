terraform {
  required_version = "~>1.9.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~>5.96"
    }
  }

  backend "local" {}
}

provider "aws" {
  region = "eu-west-2"
}

data "aws_subnets" "localstack" {}

resource "aws_ecs_cluster" "ext-dev" {
  name = "ext-dev"

  tags = {
    Env              = "Local"
    "Traefik.Enable" = "True"
  }

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_task_definition" "foo" {
  track_latest = true

  family = "foo"

  cpu    = 256
  memory = 512

  network_mode = "awsvpc"
  requires_compatibilities = [
    "FARGATE",
  ]

  container_definitions = jsonencode([
    {
      name  = "runtime",
      image = "docker.io/thewondersmith/echo-rs:latest",
      cpu   = 0,
      portMappings = [
        {
          "name" : "echo",
          "containerPort" : 8080,
          "hostPort" : 8080,
          "protocol" : "tcp"
        },
        {
          "name" : "metrics",
          "containerPort" : 9090,
          "hostPort" : 9090,
          "protocol" : "tcp"
        }
      ],
      essential = true,
      environment = [],
      dockerLabels = {
        "traefik.enable" : "true",
        "traefik.http.routers.example-http.entrypoints" : "http",
        "traefik.http.routers.example-https-0.entrypoints" : "https",
        "traefik.http.routers.example-https-1.entrypoints" : "https",
        "traefik.http.routers.example-http.middlewares" : "redirect-to-http",
        "traefik.http.middlewares.redirect-to-http.redirectscheme.port" : "443",
        "traefik.http.routers.example-https-1.tls.certResolver" : "ext-dev-acme"
        "traefik.http.routers.example-https-0.tls.certResolver" : "ext-dev-acme",
        "traefik.http.routers.example-https-0.rule" : "Host(`example.local.app`)",
        "traefik.http.middlewares.redirect-to-http.redirectscheme.scheme" : "https",
        "traefik.http.routers.example-https-1.rule" : "Host(`echo.wondersmith.io`)",
        "traefik.http.middlewares.redirect-to-http.redirectscheme.permanent" : "true",
        "traefik.http.routers.example-http.rule" : "(HostRegexp(`.*`) && !PathPrefix(`/.well-known`))",
      },
      logConfiguration = {
        "logDriver" : "awslogs",
        "options" : {
          "awslogs-group" : "foo",
          "awslogs-region" : "eu-west-2",
          "awslogs-create-group" : "true",
          "awslogs-stream-prefix" : "echo"
        }
      }
    },
    {
      name  = "sidecar",
      image = "docker.io/library/alpine:latest",
      cpu   = 0,
      command = [
        "sh",
        "-c",
        "--",
        "while true; do sleep 30; done"
      ]
      portMappings = [
        {
          "name" : "echo",
          "containerPort" : 8080,
          "hostPort" : 8080,
          "protocol" : "tcp"
        }
      ],
      essential   = false,
      environment = [],
      logConfiguration = {
        "logDriver" : "awslogs",
        "options" : {
          "awslogs-group" : "foo",
          "awslogs-region" : "eu-west-2",
          "awslogs-create-group" : "true",
          "awslogs-stream-prefix" : "sidecar"
        }
      }
    },
    {
      name  = "collector",
      image = "docker.io/library/alpine:latest",
      cpu   = 0,
      command = [
        "sh",
        "-c",
        "--",
        "while true; do sleep 30; done"
      ]
      portMappings = [
        {
          "name" : "echo",
          "containerPort" : 8080,
          "hostPort" : 8080,
          "protocol" : "tcp"
        }
      ],
      essential   = false,
      environment = [],
      logConfiguration = {
        "logDriver" : "awslogs",
        "options" : {
          "awslogs-group" : "foo",
          "awslogs-region" : "eu-west-2",
          "awslogs-create-group" : "true",
          "awslogs-stream-prefix" : "collector"
        }
      }
    },
  ])
}

resource "aws_ecs_service" "foo" {
  name                    = "foo"
  desired_count           = 1
  enable_ecs_managed_tags = true
  launch_type             = "FARGATE"
  cluster                 = aws_ecs_cluster.ext-dev.id
  task_definition         = aws_ecs_task_definition.foo.arn

  network_configuration {
    subnets = data.aws_subnets.localstack.ids
  }
}
