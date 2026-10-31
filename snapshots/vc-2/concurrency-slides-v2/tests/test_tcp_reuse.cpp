#include "network/tcp_network.hpp"
#include "network/packet_codec.hpp"
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <cassert>
#include <chrono>
#include <thread>
#include <sys/socket.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <iostream>
#include <fcntl.h>
#include <cerrno>

struct TcpNetworkTestAccess {
 static uint16_t start(TcpNetwork& n, uint16_t port=0) {
  assert(n.open_listener({"127.0.0.1",port},"test"));
  sockaddr_in a{};socklen_t len=sizeof(a);assert(getsockname(n.listener_fd_,reinterpret_cast<sockaddr*>(&a),&len)==0);
  n.configured_=true;n.start_io_thread();return ntohs(a.sin_port);
 }
 static void peer(TcpNetwork& n,uint32_t id,uint16_t port) {
  std::lock_guard<std::mutex> lock(n.mutex_);n.endpoints_[id]={"127.0.0.1",port};
 }
 static size_t connects(TcpNetwork& n){return n.connection_attempts_.load();}
 static size_t reuses(TcpNetwork& n){return n.connection_reuses_.load();}
 static void stop(TcpNetwork& n){n.close_runtime();assert(n.idle_outbound_.empty());assert(!n.has_pending_io());}
 static void error_no_replay() {
  TcpNetwork n(1,true);int fds[2];assert(socketpair(AF_UNIX,SOCK_STREAM,0,fds)==0);close(fds[1]);
  TcpNetwork::OutboundFrame f;f.destination=2;f.fd=fds[0];f.bytes={1,2,3,4};f.offset=1;
  n.outbound_peers_[2].push_back(std::move(f));n.outbound_count_=1;n.pump_outbound();
  assert(n.connection_errors_==1 && !n.has_pending_io() && n.idle_outbound_.empty());
  n.pump_outbound();assert(n.connection_attempts_==0); // No implicit replay after partial failure.
 }
 static void backpressured_data_does_not_block_control() {
  TcpNetwork n(1,true);int data[2],control[2];
  assert(socketpair(AF_UNIX,SOCK_STREAM,0,data)==0);
  assert(socketpair(AF_UNIX,SOCK_STREAM,0,control)==0);
  assert(n.set_non_blocking(data[0]));assert(n.set_non_blocking(control[0]));
  std::vector<uint8_t> fill(65536,0x22);
  while(::send(data[0],fill.data(),fill.size(),MSG_NOSIGNAL)>0){}
  assert(errno==EAGAIN || errno==EWOULDBLOCK);
  TcpNetwork::OutboundFrame bulk;bulk.destination=2;bulk.type=PacketType::DATA;bulk.fd=data[0];bulk.bytes=fill;
  TcpNetwork::OutboundFrame request;request.destination=2;request.type=PacketType::REQUEST;request.fd=control[0];request.bytes={1,2,3,4};
  const auto d=n.lane_key(2,PacketType::DATA),c=n.lane_key(2,PacketType::REQUEST);assert(d!=c);
  n.outbound_peers_[d].push_back(std::move(bulk));n.outbound_peers_[c].push_back(std::move(request));n.outbound_count_=2;
  n.pump_outbound();
  assert(n.outbound_count_==1 && n.outbound_peers_.count(d)==1 && !n.outbound_peers_.count(c));
  assert(n.outbound_peers_[d].front().offset==0 && n.outbound_peers_[d].front().would_block_calls==1);
  uint8_t got[4];assert(recv(control[1],got,4,MSG_DONTWAIT)==4);assert(got[0]==1 && got[3]==4);
  assert(n.idle_outbound_.count(c)==1 && !n.idle_outbound_.count(d));
  n.close_runtime();close(data[1]);close(control[1]);
 }
 static void fragmented_and_coalesced() {
  TcpNetwork n(1);TcpNetwork::InboundConnection c;RequestPacket p;p.request_id=99;
  auto b=PacketCodec::encode(p);std::vector<uint8_t> frame={uint8_t(b.size()>>24),uint8_t(b.size()>>16),uint8_t(b.size()>>8),uint8_t(b.size())};
  frame.insert(frame.end(),b.begin(),b.end());c.buffer.assign(frame.begin(),frame.begin()+2);n.extract_frames(c);assert(!n.receive());
  c.buffer.insert(c.buffer.end(),frame.begin()+2,frame.end());c.buffer.insert(c.buffer.end(),frame.begin(),frame.end());n.extract_frames(c);
  assert(n.receive()->request_id==99);assert(n.receive()->request_id==99);assert(!n.receive());assert(c.buffer.empty());
 }
};
static auto deadline(){return std::chrono::steady_clock::now()+std::chrono::seconds(10);}
static void send(TcpNetwork& n,uint64_t id,uint32_t dest) {
 auto p=std::make_unique<DataPacket>();p->request_id=id;p->src_node_id=1;p->key="same-key";
 p->value=Value(std::vector<uint8_t>(65536,uint8_t(id)));n.send_direct(dest,std::move(p));
}
static void receive(TcpNetwork& n,uint64_t begin,uint64_t end) {
 auto until=deadline();auto id=begin;
 while(id<end && std::chrono::steady_clock::now()<until) {
  n.begin_receive_batch();while(auto p=n.receive()) {
   assert(p->request_id==id++);auto* d=dynamic_cast<DataPacket*>(p.get());assert(d && d->key=="same-key");
   assert(d->value.data()==std::vector<uint8_t>(65536,uint8_t(p->request_id)));
  }
  std::this_thread::sleep_for(std::chrono::milliseconds(1));
 }
 assert(id==end);n.begin_receive_batch();assert(!n.receive());
}
static void mixed_lane_fifo() {
 TcpNetwork a(1,true),b(2);auto port=TcpNetworkTestAccess::start(b);TcpNetworkTestAccess::peer(a,2,port);TcpNetworkTestAccess::start(a);
 for(uint64_t i=1;i<=16;++i){send(a,i,2);auto p=std::make_unique<RequestPacket>();p->request_id=i;a.send_direct(2,std::move(p));}
 uint64_t data=1,request=1;auto until=deadline();
 while((data<=16 || request<=16) && std::chrono::steady_clock::now()<until) {
  b.begin_receive_batch();while(auto p=b.receive()) {
   if(p->type==PacketType::DATA){assert(p->request_id==data++);auto* d=dynamic_cast<DataPacket*>(p.get());assert(d->value.data()==std::vector<uint8_t>(65536,uint8_t(p->request_id)));}
   else {assert(p->type==PacketType::REQUEST && p->request_id==request++);}
  }
  std::this_thread::sleep_for(std::chrono::milliseconds(1));
 }
 assert(data==17 && request==17);assert(TcpNetworkTestAccess::connects(a)==2);assert(TcpNetworkTestAccess::reuses(a)==30);
 TcpNetworkTestAccess::stop(a);TcpNetworkTestAccess::stop(b);
}
static void mode(bool reuse) {
 TcpNetworkTestAccess::backpressured_data_does_not_block_control();
 mixed_lane_fifo();
 TcpNetwork a(1,reuse),b(2),c(3);auto port=TcpNetworkTestAccess::start(b);auto portc=TcpNetworkTestAccess::start(c);
 TcpNetworkTestAccess::peer(a,2,port);TcpNetworkTestAccess::peer(a,3,portc);TcpNetworkTestAccess::start(a);
 for(uint64_t i=1;i<=32;++i){send(a,i,2);send(a,i,3);}receive(b,1,33);receive(c,1,33);
 assert(TcpNetworkTestAccess::connects(a)==(reuse?2:64));
 assert(TcpNetworkTestAccess::reuses(a)==(reuse?62:0));
 auto until=deadline();while(a.has_pending_io()&&std::chrono::steady_clock::now()<until)std::this_thread::sleep_for(std::chrono::milliseconds(1));
 assert(!a.has_pending_io()); // Idle persistent sockets must not block quiescence.
 if(reuse) {
  // Receiver closes idle stream; next request reconnects before any new bytes.
  TcpNetworkTestAccess::stop(b);TcpNetworkTestAccess::start(b,port);std::this_thread::sleep_for(std::chrono::milliseconds(30));
  send(a,33,2);receive(b,33,34);assert(TcpNetworkTestAccess::connects(a)==3);
 }
 TcpNetworkTestAccess::stop(a);TcpNetworkTestAccess::stop(b);TcpNetworkTestAccess::stop(c);
}
int main(){TcpNetworkTestAccess::fragmented_and_coalesced();TcpNetworkTestAccess::error_no_replay();mode(false);mode(true);std::cout<<"TCP reuse tests passed\n";}
